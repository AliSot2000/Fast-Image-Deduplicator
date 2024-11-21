import datetime
import logging
import multiprocessing as mp
import os.path
import shutil
import time
from logging.handlers import QueueListener
from typing import List, Union, Callable, Dict, Optional, Tuple

import numpy as np

from fast_diff_py.base_process import GracefulWorker
from fast_diff_py.cache import ImageCache, BatchCache
from fast_diff_py.child_processes_new import FirstLoopWorker, SecondLoopWorker
from fast_diff_py.config_new import Config, Progress, FirstLoopConfig, SecondLoopConfig, SecondLoopRuntimeConfig, \
    FirstLoopRuntimeConfig
from fast_diff_py.datatransfer_new import (PreprocessResult, BatchCompareArgs, ItemCompareArgs, BatchCompareResult,
                                           ItemCompareResult)
from fast_diff_py.sqlite_db import SQLiteDB
from fast_diff_py.utils import sizeof_fmt


class FastDifPy(GracefulWorker):
    db: SQLiteDB
    config_path: str
    config: Config
    logger: logging.Logger

    handles: Union[List[mp.Process], None] = None
    exit_counter: int = 0

    # Child process perspective
    cmd_queue: Optional[mp.Queue] = None
    result_queue: mp.Queue = mp.Queue()
    logging_queue: mp.Queue = mp.Queue()
    ql: logging.handlers.QueueListener = None

    # Used for logging
    _enqueue_counter: int = 0
    _dequeue_counter: int = 0
    _last_dequeue_counter: int = 0

    # Attrs related to running the loop
    manager: mp.Manager = mp.Manager()
    ram_cache: Optional[Dict[int, BatchCache]] = None

    # The key in the first dict is the same as the ram_cache key
    # The second dict contains a key for each row in the block. The 'key' int is the key_a of the dif_table
    #
    # If they have, we move to the next block, drop the current cache index, and decrement the progress_counter by the
    # number of keys in the dict we're about to delete
    block_progress_dict: Dict[int, Dict[int, bool]] = {}

    hash_fn: Callable = None
    cpu_diff: Callable[[np.ndarray[np.uint8], np.ndarray[np.uint8]], float] = None
    gpu_diff: Callable[[np.ndarray[np.uint8], np.ndarray[np.uint8]], float] = None

    # ==================================================================================================================
    # Util
    # ==================================================================================================================

    def commit(self):
        """
        Commit the db and the config
        """
        self.db.commit()
        cfg = self.config.model_dump_json()

        if not self.config.retain_progress:
            return

        if self.config.config_path is None:
            path = os.path.join(self.config.root_dir_a, ".task.json")
        else:
            path = self.config.config_path

        with open(path, "w") as file:
            file.write(cfg)

    def get_diff_pairs(self, delta: float) -> List[Tuple[str, str, float]]:
        """
        Get the diff pairs from the database. Wrapper for db.get_duplicate_pairs.

        :param delta: The threshold for the difference

        INFO: Needs the db to exist and be connected

        :return: A list of tuples of the form (file_a, file_b, diff)
        """
        for p in self.db.get_duplicate_pairs(delta):
            yield p

    def get_diff_clusters(self, delta: float, dir_a: bool = True) -> Tuple[str, Dict[str, float]]:
        """
        Get a Cluster of Duplicates. Wrapper for db.get_cluster.

        A Cluster is characterized by a common image in either dir_a or dir_b.

        :param delta: The threshold for the difference
        :param dir_a: Whether to get the cluster for dir_a or dir_b

        INFO: Needs the db to exist and be connected

        :return: A list of tuples of the form (common_file, {file: diff})
            where common_file is either always in dir_a or dir_b and the files in the dict are in the opposite directory
        """
        for h, d in self.db.get_cluster(delta, dir_a):
            yield h, d

    def reduce_diff(self, threshold: float):
        """
        Reduce the diff table based on the threshold provided. All pairs with a higher threshold are removed.

        Wrapper for db.drop_diff

        :param threshold: The threshold for the difference
        """
        self.db.drop_diff(threshold)

    def cleanup(self):
        """
        Clean up the FastDifPy object, stopping the logging queue,
        """
        if self.config.delete_db:
            self.logger.info(f"Closing DB and deleting DB at {self.config.db_path}")
            self.db.close()
            os.remove(self.config.db_path)

        if self.config.delete_thumb:
            self.logger.info(f"Deleting Thumbnail Directory at {self.config.thumb_dir}")
            shutil.rmtree(self.config.thumb_dir)

        self.logger.info("Removing Task File")
        shutil.rmtree(self.config.thumb_dir)

        if self.ql is not None:
            self.ql.stop()

    def start_logging(self):
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.ql = QueueListener(self.logging_queue, handler, respect_handler_level=True)
        self.ql.start()

    def __init__(self, dir_a: str, dir_b: str = None, config: Config = None,
                 default_cfg_path: str = None, purge: bool = False, abort_recover: bool = True):
        """
        Initialize the FastDifPy object.
        """
        super().__init__(0)
        self.logger = logging.getLogger("FastDiffPy_Main")
        self.logger.setLevel(logging.DEBUG)

        qh = logging.handlers.QueueHandler(self.logging_queue)
        self.logger.addHandler(qh)
        self.start_logging()

        # Try to recover from the config
        if config is not None:
            self.config = config

            self.recover_from_config(abort_recover)
            return

        # Step 1:
        # Determine if there is already something in progress:
        self.config_path = os.path.join(dir_a, ".task.json") if default_cfg_path is None else default_cfg_path
        if os.path.exists(self.config_path) and not purge:
            with open(self.config_path, "r") as f:
                cfg = Config.model_validate_json(f.read())

            # Set the config
            self.config = cfg

            # Perform progress recovery
            self.recover_from_config(abort_recover)
            return

        # Step 2:
        # Create a new config and start fresh
        assert config is None, "Config should be None"
        self.config = Config(root_dir_a=dir_a, root_dir_b=dir_b)

        # Step 3
        # Path to the DB:
        p = self.config.db_path if self.config.db_path is not None else os.path.join(dir_a, ".fast_diff.db")
        if os.path.exists(p) and not purge:
            raise ValueError(f"Database already exists at {p}")

        elif os.path.exists(p) and purge:
            self.logger.info(f"Purging DB at {p}")
            shutil.rmtree(p)

        # Connect to the db.
        self.db = SQLiteDB(p, debug=__debug__)
        self.config.db_path = p

        # Populate Thumb dir, if not exists
        if self.config.thumb_dir is None:
            self.config.thumb_dir = os.path.join(dir_a, ".temp_thumb")

        # Make directory if not exists
        if not os.path.exists(self.config.thumb_dir):
            os.makedirs(self.config.thumb_dir)

        self.result_queue = mp.Queue()
        self.register_interrupts()

    def recover_from_config(self):
        """
        Recover the FastDifPy object from the config
        """
        # Check the DB
        if not os.path.exists(self.config.db_path):
            raise ValueError(f"Database does not exist at {self.config.db_path}")

        try:
            # Connect to the DB
            self.db = SQLiteDB(self.config.db_path, debug=__debug__)
        except Exception as e:
            if not self.config.state != Progress.SECOND_LOOP_POPULATING:
                self.logger.exception("Exception while connecting to the database", exc_info=e)
            self.db = None

        # Check the root dir a
        if not os.path.exists(self.config.root_dir_a):
            raise ValueError(f"Directory A does not exist at {self.config.root_dir_a}")

        # Check the root dir b
        if self.config.root_dir_b is not None and not os.path.exists(self.config.root_dir_b):
            raise ValueError(f"Directory B does not exist at {self.config.root_dir_b}")

        # Check and create plot dir
        if self.config.second_loop.make_diff_plots and not os.path.exists(self.config.second_loop.plot_output_dir):
            os.makedirs(self.config.second_loop.plot_output_dir)

        # Check the thumb dir
        if not os.path.exists(self.config.thumb_dir) \
            and self.config.state in (Progress.FIRST_LOOP_IN_PROGRESS,
                                      Progress.FIRST_LOOP_DONE,
                                      Progress.SECOND_LOOP_IN_PROGRESS,
                                      Progress.SECOND_LOOP_DONE) \
            and self.config.first_loop.compress:
                raise ValueError(f"Thumbnail directory does not exist at {self.config.thumb_dir}")

        # Make the directory otherwise
        if not os.path.exists(self.config.thumb_dir):
            os.makedirs(self.config.thumb_dir)

        # Drop the directory table and perform index again
        if self.config.state == Progress.INIT and self.db.dir_table_exists():
            self.logger.info(f"We're in the INIT state, but the directory table exists. Dropping the table")
            self.db.drop_directory_table()
            self.full_index(True)
            return

        elif self.config.state == Progress.INDEXED_DIRS:
            self.logger.info(f"Recovering from INDEXED_DIRS, checking switch of dir a and b")
            self.cond_switch_a_b()
            self.db.set_keys_zero_index()

            self.first_loop(chain=True)
            return

        elif self.config.state == Progress.FIRST_LOOP_IN_PROGRESS:
            self.logger.info(f"Recovering from FIRST_LOOP_IN_PROGRESS")
            self.db.reset_preprocessing()
            self.first_loop(chain=True)





    # ==================================================================================================================
    # Indexing
    # ==================================================================================================================

    def full_index(self, chain: bool = False):
        """
        Full index performs all actions associated with indexing the files
        """
        # Create the table
        self.db.create_directory_table_and_index()

        # Index the directories
        self.perform_index()

        # Switch the directories if necessary
        self.cond_switch_a_b()

        # Set the keys to zero index
        self.db.set_keys_zero_index()

        self.commit()

        if chain:
            self.first_loop(chain=True)

    def check_directories(self) -> bool:
        """
        Check they if they are subdirectories of each other.

        :return: True if they are subdirectories of each other
        """
        if self.config.root_dir_b is None:
            return False

        # Take absolute paths just to be sure
        abs_a = os.path.abspath(self.config.root_dir_a)
        abs_b = os.path.abspath(self.config.root_dir_b)

        dir_a = os.path.dirname(abs_a)
        dir_b = os.path.dirname(abs_b)

        # Same directory, make sure we don't have the same name
        if dir_a == dir_b:
            return os.path.basename(abs_a) == os.path.basename(abs_b)

        # Otherwise check the prefixes
        return abs_a.startswith(abs_b) or abs_b.startswith(abs_a)

    def perform_index(self):
        """
        Perform the indexing of the directories provided
        """
        # Check if the directories are subdirectories of each other
        if self.check_directories():

            self.cleanup()
            raise ValueError("The two provided subdirectories are subdirectories of each other. Cannot proceed")

        self._enqueue_counter = 0
        self._dequeue_counter = 0
        self._last_dequeue_counter = 0

        # Index the directories
        self.__recursive_index(path=self.config.root_dir_a, dir_a=True)
        if self.config.root_dir_b is not None:
            self.__recursive_index(path=self.config.root_dir_b, dir_a=False)

        self.config.state = Progress.INDEXED_DIRS
        self.commit()

    def cond_switch_a_b(self):
        """
        Conditionally switch directory a to directory b and vice versa. Needed for performance improvements.
        """
        # not necessary, if there's no dir b
        if self.config.root_dir_b is None:
            return

        # If directory a has already less or equal number to directory b, skip
        if self.db.get_dir_entry_count(False) <= self.db.get_dir_entry_count(True):
            return

        self.config.root_dir_a, self.config.root_dir_b = self.config.root_dir_b, self.config.root_dir_a
        self.db.swap_dir_b()

    def __recursive_index(self, path: str = None,
                          dir_a: bool = True,
                          ignore_thumbnail: bool = True,
                          dir_count: int = 0):
        """
        Recursively index the directories. This function is called by the index_the_dirs function.

        For speed improvements, the function will store up to `batch_size` files in ram before writing to db.
        Similarly, the function will store up to `batch_size` directories in ram before recursing.

        If the number of directories in ram is greater than `batch_size`, the function will start recursing early.
        If the number of files in ram is greater than `batch_size`, the function will write to the db early.

        :param ignore_thumbnail: If any directory at any level, starting with .temp_thumb should be ignored.
        :param dir_a: True -> Index dir A. False -> Index dir B
        :param dir_count: The number of directories in all upper stages of the recursion.

        :return:
        """
        # load the path to index from
        if path is None:
            path = self.config.root_dir_a if dir_a else self.config.root_dir_b

        dirs = []
        files = []

        for file_name in os.listdir(path):
            full_path = os.path.join(path, file_name)

            # ignore a path if given
            if full_path in self.config.ignore_paths:
                continue

            # ignoring based only on name
            if file_name in self.config.ignore_names:
                continue

            # Thumbnail directory is called .temp_thumbnails
            if file_name.startswith(".temp_thumb") and ignore_thumbnail:
                continue

            # for directories, continue the recursion
            if os.path.isdir(full_path):
                dirs.append(full_path)

            if os.path.isfile(full_path):
                # check if the file is supported, then add it to the database
                if os.path.splitext(full_path)[1].lower() in self.config.allowed_file_extensions:
                    files.append(file_name)

            # let the number of files grow to a batch size
            if len(files) > self.config.batch_size_dir:
                # Store files in the db
                self.db.bulk_insert_file(path, files, not dir_a)
                self._enqueue_counter += len(files)
                self.logger.info(f"Indexed {self._enqueue_counter} files")
                files = []

            # Start recursion early, if there is too much in RAM
            if len(dirs) + dir_count > self.config.batch_size_dir:
                # Dump the files
                self._enqueue_counter += len(files)
                self.logger.info(f"Indexed {self._enqueue_counter} files")
                self.db.bulk_insert_file(path, files, not dir_a)
                files = []

                # Recurse through the directories
                while len(dirs) > 0:
                    d = dirs.pop()
                    self.__recursive_index(path=d,
                                           dir_a=dir_a,
                                           ignore_thumbnail=ignore_thumbnail,
                                           dir_count=dir_count + len(dirs))

        # Store files in the db
        self._enqueue_counter += len(files)
        self.logger.info(f"Indexed {self._enqueue_counter} files")
        self.db.bulk_insert_file(path, files, not dir_a)

        # Recurse through the directories
        while len(dirs) > 0:
            d = dirs.pop()
            self.__recursive_index(path=d,
                                   dir_a=dir_a,
                                   ignore_thumbnail=ignore_thumbnail,
                                   dir_count=dir_count + len(dirs))

    # ==================================================================================================================
    # Multiprocessing Common
    # ==================================================================================================================

    def multiprocessing_preamble(self, prefill: Callable, first_loop: bool = False):
        """
        Set up the multiprocessing environment
        """
        # reset counters
        self.exit_counter = 0
        if first_loop:
            self.cmd_queue = mp.Queue(maxsize=self.config.batch_size_max_fl)
        else:
            self.cmd_queue = mp.Queue()

        # Prefill the command queue
        prefill()

        # Create Worker Objects
        if first_loop:
            workers = []
            for i in range(self.config.first_loop.cpu_proc):
                workers.append(FirstLoopWorker(
                    identifier=i,
                    compress=self.config.first_loop.compress,
                    do_hash=self.config.first_loop.compute_hash,
                    target_size=(self.config.compression_target_x, self.config.compression_target_y),
                    cmd_queue=self.cmd_queue,
                    res_queue=self.result_queue,
                    log_queue=self.logging_queue,
                    shift_amount=self.config.first_loop.shift_amount,
                    log_level=self.config.log_level_children,
                    hash_fn=self.hash_fn,
                    thumb_dir=self.config.thumb_dir,
                    timeout=self.config.child_proc_timeout))

            self.handles = [mp.Process(target=w.main) for w in workers]
        else:
            workers = []
            if self.cpu_diff is None:
                import fast_diff_py.img_processing as imgp
                self.cpu_diff = imgp.mse

            if self.config.second_loop.gpu_proc > 0 and self.gpu_diff is None:
                import fast_diff_py.img_processing_gpu as imgpg
                self.gpu_diff = imgpg.mse_gpu

            for i in range(self.config.second_loop.cpu_proc + self.config.second_loop.gpu_proc):
                workers.append(SecondLoopWorker(
                    identifier=i,
                    cmd_queue=self.cmd_queue,
                    res_queue=self.result_queue,
                    log_queue=self.logging_queue,
                    is_compressed=self.config.first_loop.compress,
                    compare_fn=self.cpu_diff if i < self.config.second_loop.cpu_proc else self.gpu_diff,
                    target_size=(self.config.compression_target_x, self.config.compression_target_y),
                    log_level=self.config.log_level_children,
                    timeout=self.config.child_proc_timeout,
                    has_dir_b=self.config.root_dir_b is not None,
                    plot_dir=self.config.second_loop.plot_output_dir,
                    ram_cache=self.ram_cache,
                    thumb_dir=self.config.thumb_dir if self.config.first_loop.compress else None,
                    batched_args=self.config.second_loop.batch_args,
                    plot_threshold=self.config.second_loop.diff_threshold))

            self.handles = [mp.Process(target=w.main) for w in workers]

        # Start the processes
        for h in self.handles:
            h.start()

    def send_stop_signal(self):
        """
        Send the stop signal to the child processes
        """
        for _ in self.handles:
            self.cmd_queue.put(None)

    def multiprocessing_epilogue(self):
        """
        Wait for the child processes to stop and join them
        """
        one_alive = True
        timeout = 30

        # Waiting for processes to finish
        while one_alive:

            # Check liveliness of the processes
            one_alive = False
            for h in self.handles:
                if h.is_alive():
                    one_alive = True
                    break

            # Wait until timeout
            time.sleep(1)
            timeout -= 1

            # Timeout - break out
            if timeout <= 0:
                break

        # Join the processes and kill them on timeout
        for h in self.handles:
            if timeout > 0:
                h.join()
            else:
                h.kill()
                h.join()

        # Reset the handles
        self.handles = None
        self.cmd_queue = None

    def generic_mp_loop(self, first_iteration: bool = True, benchmark: bool = False):
        """
        Generic Loop using multiprocessing.
        """
        enqueue_time = 0
        dequeue_time = 0
        task = "Images" if first_iteration else "Pairs"

        self._enqueue_counter = 0
        self._dequeue_counter = 0
        self._last_dequeue_counter = 0

        # defining the two main functions for the loop
        submit_fn = self.submit_batch_first_loop if first_iteration else self.second_loop_load_batch
        dequeue_fn = self.dequeue_results_first_loop if first_iteration else self.dequeue_second_loop

        # Set up the multiprocessing environment
        self.multiprocessing_preamble(submit_fn, first_loop=first_iteration)

        # ==============================================================================================================
        # Benchmarking implementation
        # ==============================================================================================================
        if benchmark:
            start = datetime.datetime.now(datetime.UTC)
            while self.run:
                # Nothing left to submit
                s = datetime.datetime.now(datetime.UTC)
                if not submit_fn():
                    break

                enqueue_time += (datetime.datetime.now(datetime.UTC) - s).total_seconds()

                bs = self.config.first_loop.batch_size if first_iteration else self.config.second_loop.batch_size
                if self._dequeue_counter > self._last_dequeue_counter + bs / 4:
                    self.logger.info(f"Enqueued: {self._enqueue_counter} {task}")
                    self.logger.info(f"Done with {self._dequeue_counter} {task}")
                    self._last_dequeue_counter = self._dequeue_counter

                # Precondition -> Two times batch-size has been submitted to the queue
                s = datetime.datetime.now(datetime.UTC)
                dequeue_fn()
                dequeue_time += (datetime.datetime.now(datetime.UTC) - s).total_seconds()
                self.commit()

            # Send the stop signal
            self.send_stop_signal()

            # waiting for pipeline to empty
            while self.exit_counter < len(self.handles):
                s = datetime.datetime.now(datetime.UTC)
                dequeue_fn(drain=True)
                dequeue_time += (datetime.datetime.now(datetime.UTC) - s).total_seconds()

                bs = self.config.first_loop.batch_size if first_iteration else self.config.second_loop.batch_size
                if self._dequeue_counter > self._last_dequeue_counter + bs / 4:
                    self.logger.info(f"Enqueued: {self._enqueue_counter} {task}")
                    self.logger.info(f"Done with {self._dequeue_counter} {task}")
                    self._last_dequeue_counter = self._dequeue_counter

                self.commit()

            self.commit()
            self.multiprocessing_epilogue()

            end = datetime.datetime.now(datetime.UTC)
            tsk_str = "First Loop" if first_iteration else "Second Loop"
            self.logger.debug(f"Statistics for {tsk_str}")
            self.logger.debug(f"Time Taken: {(end - start).total_seconds()}", )
            self.logger.debug(f"Enqueue Time: {enqueue_time}")
            self.logger.debug(f"Dequeue Time: {dequeue_time}", )

            return

        # ==============================================================================================================
        # Normal implementation
        # ==============================================================================================================
        while self.run:
            if not submit_fn():
                break

            bs = self.config.first_loop.batch_size if first_iteration else self.config.second_loop.batch_size
            if self._dequeue_counter > self._last_dequeue_counter + bs / 4:
                self.logger.info(f"Enqueued: {self._enqueue_counter} {task}")
                self.logger.info(f"Done with {self._dequeue_counter} {task}")
                self._last_dequeue_counter = self._dequeue_counter

            # Precondition -> Two times batch-size has been submitted to the queue
            dequeue_fn()
            self.commit()

        # Send the stop signal
        self.send_stop_signal()

        # waiting for pipeline to empty
        while self.exit_counter < len(self.handles):
            dequeue_fn(drain=True)

            bs = self.config.first_loop.batch_size if first_iteration else self.config.second_loop.batch_size
            if self._dequeue_counter > self._last_dequeue_counter + bs / 4:
                self.logger.info(f"Enqueued: {self._enqueue_counter} {task}")
                self.logger.info(f"Done with {self._dequeue_counter} {task}")
                self._last_dequeue_counter = self._dequeue_counter

            self.commit()

        self.commit()
        self.multiprocessing_epilogue()

    # ==================================================================================================================
    # First Loop
    # ==================================================================================================================

    def build_first_loop_runtime_config(self, cfg: FirstLoopConfig):
        """
        Check the configuration for the first loop

        :param cfg: The configuration to check

        :return: True if the configuration is valid and the first loop can run
        """
        if cfg.compute_hash and cfg.shift_amount == 0:
            self.logger.warning("Shift amount is 0, but hash computation is requested. "
                                "Only exact Matches will be found")

        todo = self.db.get_dir_entry_count(False) + self.db.get_dir_entry_count(True)
        rtc = FirstLoopRuntimeConfig.model_validate(cfg.model_dump())

        # We are in a case where we have less than the number of CPUs
        if todo < os.cpu_count():
            self.logger.debug("Less than the number of CPUs available. Running sequentially")
            rtc.parallel = False

        # We have less than a significant amount of batches, submission done separately
        if todo / os.cpu_count() < 40:
            self.logger.debug("Less than 40 images / cpu available. No batching")
            rtc.batch_size = None

        else:
            rtc.batch_size = min(self.config.batch_size_max_fl, int(todo / 4 / os.cpu_count()))
            self.logger.debug(f"Batch size set to: {rtc.batch_size}")

        return rtc

    def print_fs_usage(self, do_print: bool = True) -> int:
        """
        Function used to print the amount storage used by the thumbnails.
        """
        dir_a_count = self.db.get_dir_entry_count(False)
        dir_b_count = self.db.get_dir_entry_count(True)

        if do_print:
            self.logger.info(f"Entries in {self.config.root_dir_a}: {dir_a_count}")

        if dir_b_count > 0 and do_print:
            self.logger.info(f"Entries in {self.config.root_dir_b}: {dir_b_count}")
            self.logger.info(f"Total Entries: {dir_a_count + dir_b_count}")

        total = (dir_a_count + dir_b_count) * self.config.compression_target_x * self.config.compression_target_y * 3
        if do_print:
            self.logger.info(f"Total Storage Usage: {sizeof_fmt(total)}")

        return total

    def sequential_first_loop(self):
        """
        Run the first loop sequentially
        """
        # Update the state
        self.cmd_queue = mp.Queue()
        self.config.state = Progress.FIRST_LOOP_IN_PROGRESS
        self._enqueue_counter = 0
        self._dequeue_counter = 0
        self._last_dequeue_counter = 0

        processor = FirstLoopWorker(
            identifier=-1,
            compress=self.config.first_loop.compress,
            do_hash=self.config.first_loop.compute_hash,
            target_size=(self.config.compression_target_x, self.config.compression_target_y),
            cmd_queue=self.cmd_queue,
            res_queue=self.result_queue,
            log_queue=self.logging_queue,
            shift_amount=self.config.first_loop.shift_amount,
            log_level=self.config.log_level_children,
            hash_fn=self.hash_fn,
            thumb_dir=self.config.thumb_dir,
            timeout=self.config.child_proc_timeout)

        while self.run:
            # Get the next batch
            args = self.db.batch_of_preprocessing_args(batch_size=self.config.first_loop.batch_size)

            # No more arguments
            if len(args) == 0:
                break

            # Process the batch
            results = []
            for a in args:
                # Process the arguments
                if self.config.first_loop.compress and self.config.first_loop.compute_hash:
                    r = processor.compress_and_hash(a)
                elif self.config.first_loop.compress:
                    r = processor.compress_only(a)
                elif self.config.first_loop.compute_hash:
                    r = processor.compute_hash(a)
                else:
                    raise ValueError("No computation requested")

                results.append(r)

            # Store the results
            self.store_batch_first_loop(results)

        self.cmd_queue = None
        if self.run:
            self.config.state = Progress.FIRST_LOOP_DONE

            # Reset the config
            self.config.first_loop = FirstLoopRuntimeConfig.model_validate(self.config.first_loop.model_dump())

    def first_loop(self, config: Union[FirstLoopConfig, FirstLoopRuntimeConfig] = None, chain: bool = False):
        """
        Run the first loop

        :param config: The configuration for the first loop
        """
        # Set the config
        if config is not None:
            self.config.first_loop = config

        # Build runtime config if necessary
        if isinstance(self.config.first_loop, FirstLoopConfig):
            self.config.first_loop = self.build_first_loop_runtime_config(self.config.first_loop)

        # No computation required. Skip it.
        if not (self.config.first_loop.compress or self.config.first_loop.compute_hash):
            self.logger.info("No computation required. Skipping first loop")
            return

        # Create hash table if necessary
        if self.config.first_loop.compute_hash:
            self.db.create_hash_table_and_index()

        # Sequential First Loop requested
        if not self.config.first_loop.parallel:
            self.sequential_first_loop()
            return

        # Update the state
        self.config.state = Progress.FIRST_LOOP_IN_PROGRESS

        self.generic_mp_loop(first_iteration=True, benchmark=False)

        # Set the state if self.run is still true
        if self.run:
            self.config.state = Progress.FIRST_LOOP_DONE

            # Reset the config
            self.config.first_loop = FirstLoopConfig.model_validate(self.config.first_loop.model_dump())

        if chain:
            self.second_loop()

    def submit_batch_first_loop(self) -> bool:
        """
        Submit up to a batch of files to the first loop
        """
        args = self.db.batch_of_preprocessing_args(batch_size=self.config.first_loop.batch_size)

        # Submit the arguments
        if self.config.first_loop.batch_size is not None:
            if len(args) == self.config.first_loop.batch_size:
                self.cmd_queue.put(args)
            else:
                for a in args:
                    self.cmd_queue.put(a)
        else:
            for a in args:
                self.cmd_queue.put(a)
        self._enqueue_counter += len(args)

        # Return whether there are more batches to submit
        return len(args) > 0

    def dequeue_results_first_loop(self, drain: bool = False):
        """
        Dequeue the results of the first loop
        """
        results = []

        while (not self.result_queue.empty()
               and (self._dequeue_counter + self.config.first_loop.batch_size * 2 < self._enqueue_counter or drain)):
            res = self.result_queue.get()

            # Handle the cases, when result is None -> indicating a process is exiting
            if res is None:
                self.exit_counter += 1
                continue

            if isinstance(res, list):
                results.extend(res)
                self._dequeue_counter += len(res)
            else:
                results.append(res)
                self._dequeue_counter += 1

        self.store_batch_first_loop(results)

    def store_batch_first_loop(self, results: List[PreprocessResult]):
        """
        Store the results of the first loop in the database
        """
        # Check the hashes, if they should be computed
        if self.config.first_loop.compute_hash:
            # Extract all hashes from the results
            hashes = []
            for res in results:
                hashes.append(res.hash_0)
                hashes.append(res.hash_90)
                hashes.append(res.hash_180)
                hashes.append(res.hash_270)

            # Put the hashes into the db
            self.db.bulk_insert_hashes(hashes)
            lookup = self.db.get_bulk_hash_lookup(set(hashes))

            # Update the hashes from string to int (based on the hash key in the db
            for res in results:
                res.hash_0 = lookup[res.hash_0]
                res.hash_90 = lookup[res.hash_90]
                res.hash_180 = lookup[res.hash_180]
                res.hash_270 = lookup[res.hash_270]

        self.db.batch_of_first_loop_results(results, has_hash=self.config.first_loop.compute_hash)

    # ==================================================================================================================
    # Second Loop
    # ==================================================================================================================

    def second_loop(self, **kwargs):
        """
        Run the second loop
        """
        # Set the configuration
        if "config" in kwargs:
            self.config.second_loop = SecondLoopRuntimeConfig.model_validate(kwargs["config"])
        else:
            self.config.second_loop = self.second_loop_arg(**kwargs)

        # Check the configuration
        if not self.check_second_loop_config(self.config.second_loop):
            return

        # Need to populate the cache before the second loop workers are instantiated
        if self.config.second_loop.use_ram_cache:
            self.ram_cache = self.manager.dict()
        else:
            self.ram_cache = None

        # Run the second loop
        self.internal_second_loop()

    def check_second_loop_config(self, cfg: Union[SecondLoopConfig, SecondLoopRuntimeConfig]):
        """
        Check if the configured parameters are actually compatible
        """
        if isinstance(cfg, SecondLoopConfig):
            cfg = SecondLoopRuntimeConfig.model_validate(cfg.model_dump())

        if self.config.do_second_loop:
            return False

        # Check constraint on optimizations
        if cfg.match_aspect_by != -1.0 or cfg.skip_matching_hash:
            if cfg.batch_args:
                self.logger.error("Cannot skip matching hash or non-matching aspect ratio with batched processing")
                return False

        if cfg.make_diff_plots:
            if cfg.plot_output_dir is None or cfg.diff_threshold is None:
                self.logger.error("Need plot output directory and diff threshold to make diff plots")
                return False

            if cfg.batch_args:
                self.logger.error("Cannot make diff plots with batched processing")
                return False

        if cfg.cpu_proc + cfg.gpu_proc < 1:
            self.logger.error("Need at least one process to run the second loop")
            return False

        if self.config.first_loop.compress is False:
            self.logger.error("Cannot run the second loop without compression")
            return False

        # Create the plot output directory
        if not os.path.exists(cfg.plot_output_dir):
            os.makedirs(cfg.plot_output_dir)

        return True

    def second_loop_arg(self,
                        cpu_proc: int = None,
                        gpu_proc: int = None,
                        batch_size: int = None,
                        skip_matching_hash: bool = None,
                        match_aspect_by: float = None,
                        make_diff_plots: bool = None,
                        plot_output_dir: str = None,
                        diff_threshold: float = None,
                        batch_args: bool = None,
                        use_ram_cache: bool = None,
                        parallel: bool = None,
                        ) -> SecondLoopRuntimeConfig:

        if skip_matching_hash is not None or match_aspect_by is not None:
            if batch_args is True:
                raise ValueError("Cannot skip matching hash or non-matching aspect ratio with batched processing")

        if make_diff_plots is not None:
            if plot_output_dir is None or diff_threshold is None:
                raise ValueError("Need plot output directory and diff threshold to make diff plots")

            if batch_args is True:
                raise ValueError("Cannot make diff plots with batched processing")

        if not parallel:
            if use_ram_cache is not None and use_ram_cache is False:
                raise ValueError("Cannot run without parallel processing and without ram cache")

            if batch_args is not None and batch_args is True:
                raise ValueError("Cannot run without parallel processing and with batched processing")

        if cpu_proc is None:
            cpu_proc = os.cpu_count()
        if gpu_proc is None:
            gpu_proc = 0

        # One direction is constrained beyond the other
        if batch_size is None:
            if self.db.get_dir_entry_count(False) < cpu_proc + gpu_proc:
                # Very small case, we don't need full speed.
                if self.db.get_dir_entry_count(True) < cpu_proc + gpu_proc:
                    parallel = False

                batch_size = min(self.db.get_dir_entry_count(True) // 4, self.config.batch_size_max_sl)
            else:
                batch_size = min(self.db.get_dir_entry_count(True),
                                 self.db.get_dir_entry_count(False),
                                 self.config.batch_size_max_sl)

        args = {"cpu_proc": cpu_proc,
                "gpu_proc": gpu_proc,
                "skip_matching_hash": skip_matching_hash,
                "match_aspect_by": match_aspect_by,
                "make_diff_plots": make_diff_plots,
                "plot_output_dir": plot_output_dir,
                "diff_threshold": diff_threshold,
                "batch_args": batch_args,
                "batch_size": batch_size,
                "use_ram_cache": use_ram_cache,
                "parallel": parallel}

        non_empty = {k: v for k, v in args.items() if v is not None}
        return SecondLoopRuntimeConfig.model_validate(non_empty)

    def internal_second_loop(self):
        """
        Set up the second loop
        """
        # Instantiate new Config
        if not isinstance(self.config.second_loop, SecondLoopRuntimeConfig):
            self.config.second_loop = SecondLoopRuntimeConfig.model_validate(self.config.second_loop.model_dump())

        # Update the database
        self.logger.info("Prepopulating the diff table. Do not Interrupt")
        self.db.prepopulate_diff_table(has_dir_b=self.config.root_dir_b is not None,
                                       block_size=self.config.second_loop.batch_size)
        self.db.commit()
        self.logger.info(f"Done with prepopulating the diff table")

        if self.config.second_loop.parallel is False:
            self.sequential_second_loop()

        # Set the function pointers
        self.set_dequeue_second_loop()
        self.set_load_batch()
        self.config.state = Progress.SECOND_LOOP_IN_PROGRESS
        self.logger.info(f"Number of Pairs to Compare for Second Loop: {self.db.get_pair_count_diff()}")

        # Run the second loop
        self.generic_mp_loop(first_iteration=False, benchmark=False)

        if self.run:
            self.config.state = Progress.SECOND_LOOP_DONE

    def sequential_second_loop(self):
        """
        Sequential implementation of the second loop
        """
        # Set the MSE function
        if self.cpu_diff is None:
            import fast_diff_py.img_processing as imgp
            self.cpu_diff = imgp.mse

        self.config.state = Progress.SECOND_LOOP_IN_PROGRESS

        # Set the counters
        self._enqueue_counter = 0
        self._dequeue_counter = 0
        self._last_dequeue_counter = 0

        # Set up the worker
        self.cmd_queue = mp.Queue()
        self.ram_cache = {}

        slw = SecondLoopWorker(
            identifier=1,
            cmd_queue=self.cmd_queue,
            res_queue=self.result_queue,
            log_queue=self.logging_queue,
            is_compressed=self.config.first_loop.compress,
            compare_fn=self.cpu_diff,
            target_size=(self.config.compression_target_x, self.config.compression_target_y),
            has_dir_b=self.config.root_dir_b is not None,
            ram_cache=self.ram_cache,
            plot_dir=self.config.second_loop.plot_output_dir,
            batched_args=self.config.second_loop.batch_args,
            thumb_dir=self.config.thumb_dir,
            plot_threshold=self.config.second_loop.diff_threshold,
            log_level=self.config.log_level_children,
            timeout=self.config.child_proc_timeout)

        while self.run:
            # Get the next batch
            args = self.__item_block(submit=False)

            # Done?
            if len(args) == 0:
                break

            # Process the batch
            results = [slw.process_item(a) for a in args]

            # Update count
            self._enqueue_counter += len(args)
            self._dequeue_counter += len(args)

            # Update info
            self.logger.info("Done with {self._dequeue_counter} Pairs")

            # Store the results
            self.store_item_second_loop(results)

        self.config.state = Progress.SECOND_LOOP_DONE

        self.cmd_queue = None

    def set_load_batch(self):
        """
        Set the function to be used to load a batch of tasks for the workers.
        """
        batch, cache, thumb = (self.config.second_loop.batch_args,
                               self.config.second_loop.use_ram_cache,
                               self.config.first_loop.compress)

        if batch and cache and thumb:
            self.second_loop_load_batch = self.__batched_thumb_block
        elif batch and cache and not thumb:
            self.second_loop_load_batch = self.__batched_org_block
        elif batch and not cache and thumb:
            self.second_loop_load_batch = self.__batched_thumb_block
        elif batch and not cache and not thumb:
            self.second_loop_load_batch = self.__batched_org_block

        elif not batch and cache and thumb:
            self.second_loop_load_batch = lambda: self.__item_block(submit=True)
        elif not batch and cache and not thumb:
            self.second_loop_load_batch = lambda: self.__item_block(submit=True)
        elif not batch and not cache and thumb:
            self.second_loop_load_batch = lambda: self.__item_block(submit=True)
        elif not batch and not cache and not thumb:
            self.second_loop_load_batch = lambda: self.__item_block(submit=True)
        else:
            raise ValueError("Tertiem Non Datur - This should not be possible")

    def dequeue_second_loop(self):
        """
        Placeholder for the dequeue function. The function is set using the set_dequeue_second_loop.

        If set_dequeue_second_loop is not called, this function will raise a NotImplementedError
        """
        raise NotImplementedError("Function pointer to be called for dequeue_second_loop"
                                  " need to call set_dequeue_second_loop")

    def set_dequeue_second_loop(self):
        """
        Set the dequeue function for the second loop
        """
        if self.config.second_loop.batch_args:
            self.dequeue_second_loop = self.dequeue_second_loop_batch
        else:
            self.dequeue_second_loop = self.dequeue_second_loop_item

    def second_loop_load_batch(self) -> bool:
        """
        This function loads the next batch of images into the cache.
        It populates the command queue with the matching args and returns whether new images successfully enqueued
        or if we're done.

        - The function distinguishes between cases when we have precomputed thumbnails and when not
        - The function distinguishes when we're able to submit batch-jobs and when we're able to submit single jobs

        This is a placeholder function. set_load_batch needs to be called to set the function pointer.

        :return: True if we loaded a block, False if we're done
        """
        raise NotImplementedError("Function pointer to be called for load_batch needs to call set_load_batch for "
                                  "it to be set")

    # ==================================================================================================================
    # Second Loop Cache Functions
    # ==================================================================================================================

    def __build_thumb_cache(self, l_x: int, l_y: int, s_x: int, s_y: int):
        """
        Build the thumbnail cache for cases when we're using ram cache
        """
        # Using ram cache, we need to prepare the caches
        assert self.config.second_loop.use_ram_cache and self.config.first_loop.compress, \
            "Precondition for building thumbnail cache not met"

        # check we're on the diagonal
        if l_x + 1 == l_y:

            # Perform sanity check
            if not s_x == s_y:
                raise ValueError("The block is not a square")

            l = l_x
            s = s_x + 1

            cache = ImageCache(offset=l,
                               size=s,
                               img_shape=(self.config.compression_target_x, self.config.compression_target_y, 3))

            # Load the cache
            cache.fill_thumbnails(thumbnail_dir=self.config.thumb_dir)

            # Create the x-y cache object
            bc = BatchCache(x=cache, y=cache)

        else:
            # We're not on the diagonal
            x = ImageCache(offset=l_x,
                           size=s_x,
                           img_shape=(self.config.compression_target_x, self.config.compression_target_y, 3))

            y = ImageCache(offset=l_y,
                           size=s_y,
                           img_shape=(self.config.compression_target_x, self.config.compression_target_y, 3))

            # Load the cache
            x.fill_thumbnails(thumbnail_dir=self.config.thumb_dir)
            y.fill_thumbnails(thumbnail_dir=self.config.thumb_dir)

            # Create the x-y cache object
            bc = BatchCache(x=x, y=y)

        # In batched mode, we need to submit the block progress
        if self.config.second_loop.batch_args:
            # Prep the block progress dict
            bp = {i + l_x: False for i in range(s_x)}
            self.block_progress_dict[self.config.second_loop.cache_index] = bp

        self.ram_cache[self.config.second_loop.cache_index] = bc

    def __build_org_cache(self, l_x: int, l_y: int, p_x: List[str], p_y: List[str]):
        """
        Build the original cache for cases when we're using ram cache
        """
        # Using ram cache, we need to prepare the caches
        assert self.config.second_loop.use_ram_cache and not self.config.first_loop.compress, \
            "Precondition for building original cache not met"

        # check we're on the diagonal
        if l_x + 1 == l_y:

            # Perform sanity check
            if not len(p_x) == len(p_y):
                raise ValueError("The block is not a square")

            assert set([p_x[0]] + p_y) == set(p_x) | set(p_y), "The paths are not the same"

            p = [p_x[0]] + p_y
            s = len(p)
            l = l_x
            cache = ImageCache(offset=l,
                               size=s,
                               img_shape=(self.config.compression_target_x, self.config.compression_target_y, 3))

            # Load the cache
            cache.fill_original(p)

            # Create the x-y cache object
            bc = BatchCache(x=cache, y=cache)

        else:
            # We're not on the diagonal
            x = ImageCache(offset=l_x,
                           size=len(p_x),
                           img_shape=(self.config.compression_target_x, self.config.compression_target_y, 3))

            y = ImageCache(offset=l_y,
                           size=len(p_y),
                           img_shape=(self.config.compression_target_x, self.config.compression_target_y, 3))

            # Load the cache
            x.fill_original(p_x)
            y.fill_original(p_y)

            # Create the x-y cache object
            bc = BatchCache(x=x, y=y)

        # In batched mode, we need to submit the block progress
        if self.config.second_loop.batch_args:
            # Prep the block progress dict
            bp = {i + l_x: False for i in range(len(p_x))}
            self.block_progress_dict[self.config.second_loop.cache_index] = bp

        self.ram_cache[self.config.second_loop.cache_index] = bc

    def prune_cache_batch(self):
        """
        Go through the ram cache and remove the cache who's results are complete.
        """
        # Guard since we're min doesn't like empty lists
        if len(self.ram_cache.keys()) == 0:
            return

        lowest_key = min(self.ram_cache.keys())

        # Check if all keys in the block progress dict are True
        if all(self.block_progress_dict[lowest_key].values()):
            self.ram_cache.pop(lowest_key)
            self.block_progress_dict.pop(lowest_key)

    def prune_cache_item(self):
        """
        Prune the cache when we're comparing items
        """
        # Guard since we're min doesn't like empty lists
        if len(self.ram_cache.keys()) == 0:
            return
        lowest_key = min(self.ram_cache.keys())

        # Check if all keys in the block progress dict are True
        if self.db.verify_item_block(lowest_key):
            self.ram_cache.pop(lowest_key)

    # ==================================================================================================================
    # Build Second Loop Args
    # ==================================================================================================================

    def __batched_thumb_block(self):
        """
        Submit a batch of thumbnails. Depending on whether we have a cache or not, we're going to also build a cache
        """
        assert self.config.first_loop.compress and self.config.second_loop.batch_args, \
            "Precondition for batched thumb block not met"

        l_x, l_y, s_x, s_y = self.db.get_cache_block_thumb(block_key=self.config.second_loop.cache_index,
                                                           has_dir_b=self.config.root_dir_b is not None)
        self.logger.debug(f"lower_x: {l_x}, lower_y: {l_y}, Cache_Key:  {self.config.second_loop.cache_index}")
        # Stopping criterion
        if (l_x, l_y, s_x, s_y) == (-1, -1, -1, -1):
            return False

        # Retrieving the args
        args = self.db.get_task_block_key(block_key=self.config.second_loop.cache_index)

        if self.config.second_loop.use_ram_cache:
            self.__build_thumb_cache(l_x, l_y, s_x, s_y)

            # Build and submit the Args with cache index
            for key, min_key_a, max_key_b in args:
                self.cmd_queue.put(
                    BatchCompareArgs(key=key,
                                     key_a=min_key_a,
                                     key_b=max_key_b,
                                     max_size_b=s_y,
                                     cache_key=self.config.second_loop.cache_index))

        else:
            # Build args without cache index
            for key, min_key_a, max_key_b in args:
                self.cmd_queue.put(
                    BatchCompareArgs(key=key,
                                     key_a=min_key_a,
                                     key_b=max_key_b,
                                     max_size_b=s_y))

        # Increment cache index
        self.config.second_loop.cache_index += 1
        self._enqueue_counter += len(args)
        return True

    def __batched_org_block(self):
        """
        Submit a batch of originals to the second loop
        """
        assert not self.config.first_loop.compress and self.config.second_loop.batch_args, \
            "Precondition for batched original block not met"

        l_x, l_y, p_x, p_y = self.db.get_cache_block_original(block_key=self.config.second_loop.cache_index,
                                                              has_dir_b=self.config.root_dir_b is not None)
        self.logger.debug(f"lower_x: {l_x}, lower_y: {l_y}, Cache_Key:  {self.config.second_loop.cache_index}")
        # Stopping criterion
        if (l_x, l_y, p_x, p_y) == (-1, -1, [], []):
            return False

        args = self.db.get_task_block_key(block_key=self.config.second_loop.cache_index)
        assert len(args) == len(p_x), "The number of paths and keys do not match"

        if self.config.second_loop.use_ram_cache:
            self.__build_org_cache(l_x, l_y, p_x, p_y)

            for key, min_key_a, max_key_b in args:
                self.cmd_queue.put(
                    BatchCompareArgs(key=key,
                                     key_a=min_key_a,
                                     key_b=max_key_b,
                                     max_size_b=len(p_y),
                                     cache_key=self.config.second_loop.cache_index))

        else:
            for i in range(len(args)):
                key, min_key_a, max_key_b = args[i]
                self.cmd_queue.put(
                    BatchCompareArgs(key=key,
                                     key_a=min_key_a,
                                     key_b=max_key_b,
                                     max_size_b=len(p_y),
                                     path_a=p_x[i],
                                     path_b=p_y))

        # Increment cache index
        self.config.second_loop.cache_index += 1
        self._enqueue_counter += len(args)
        return True

    def __item_block(self, submit: bool = True) -> Union[bool, List[ItemCompareArgs]]:
        """
        Submit a block of items to the second loop

        :param submit: Whether to submit the items to the queue or return them (for sequential implementation)
        """
        assert self.config.second_loop.batch_args is False, "Precondition for item block not met"

        # Build caches if needed
        if self.config.second_loop.use_ram_cache:

            # Build cache for thumbnails
            if self.config.first_loop.compress:
                l_x, l_y, s_x, s_y = self.db.get_cache_block_thumb(block_key=self.config.second_loop.cache_index,
                                                                   has_dir_b=self.config.root_dir_b is not None)
                # Stopping criterion
                if (l_x, l_y, s_x, s_y) == (-1, -1, -1, -1):
                    return False
                self.__build_thumb_cache(l_x, l_y, s_x, s_y)
            else:
                # Build cache for originals
                l_x, l_y, p_x, p_y = self.db.get_cache_block_original(block_key=self.config.second_loop.cache_index,
                                                                      has_dir_b=self.config.root_dir_b is not None)

                # Stopping criterion
                if (l_x, l_y, p_x, p_y) == (-1, -1, [], []):
                    return False

                self.__build_org_cache(l_x, l_y, p_x, p_y)

        # Get the args
        args = self.db.get_item_block(block_key=self.config.second_loop.cache_index,
                                      include_block_key=self.config.second_loop.use_ram_cache)

        # Stopping criterion
        if len(args) == 0:
            return False

        # Build the args
        if self.config.second_loop.use_ram_cache:
            wrapped_args = []

            for key, key_a, key_b, path_a, path_b, block_key in args:
                wrapped_args.append(
                    ItemCompareArgs(key=key,
                                    key_a=key_a,
                                    key_b=key_b,
                                    path_a=path_a,
                                    path_b=path_b,
                                    cache_key=block_key))
        else:
            wrapped_args = []
            for key, key_a, key_b, path_a, path_b in args:
                wrapped_args.append(
                    ItemCompareArgs(key=key,
                                    key_a=key_a,
                                    key_b=key_b,
                                    path_a=path_a,
                                    path_b=path_b))
        # Submit or return the args
        if submit:
            for i in range(0, len(wrapped_args), self.config.second_loop.batch_size):
                if len(wrapped_args[i:i + self.config.second_loop.batch_size]) == self.config.second_loop.batch_args:
                    self.cmd_queue.put(wrapped_args[i:i + self.config.second_loop.batch_size])
                else:
                    for a in wrapped_args[i:i + self.config.second_loop.batch_size]:
                        self.cmd_queue.put(a)
            self._enqueue_counter += len(wrapped_args)
        else:
            self.config.second_loop.cache_index += 1
            return wrapped_args

        # Default return True
        self.config.second_loop.cache_index += 1
        return True

    # ==================================================================================================================
    # Second Loop Result Processing
    # ==================================================================================================================

    def dequeue_second_loop_item(self, drain: bool = False):
        """
        Dequeue the results of the second loop

        :param drain: Whether to drain the queue (disregard the diff between the enqueue and dequeue counters)
        """
        results = []
        count = 0

        while (not self.result_queue.empty() and
               (self._dequeue_counter + (self.config.second_loop.batch_size ** 2) * 2 < self._enqueue_counter or drain)):
            res: Union[ItemCompareResult, None, List[ItemCompareResult]] = self.result_queue.get()

            # Handle the cases, when result is None -> indicating a process is exiting
            if res is None:
                self.exit_counter += 1
                continue

            if isinstance(res, list):
                results.extend(res)
                count += len(res)
            else:
                results.append(res)
                count += 1

        self.store_item_second_loop(results)
        self._dequeue_counter += count

    def dequeue_second_loop_batch(self, drain: bool = False):
        """
        Dequeue the results of second loop.

        :param drain: Whether to drain the queue (disregard the diff between the enqueue and dequeue counters)

        """
        results: List[BatchCompareResult] = []

        while (not self.result_queue.empty()
               and (self._dequeue_counter + self.config.second_loop.batch_size * len(self.handles) < self._enqueue_counter
                    or drain)):
            res: Union[BatchCompareResult, None] = self.result_queue.get()

            # Handle the cases, when result is None -> indicating a process is exiting
            if res is None:
                self.exit_counter += 1
                continue

            results.append(res)
            self._dequeue_counter += 1

        self.store_batch_second_loop(results)

    def store_batch_second_loop(self, results: List[BatchCompareResult]):
        """
        Store the results of the second loop in the database
        """
        for res in results:
            self.db.insert_batch_diff_block_result(min_key_x=res.key_a,
                                                   max_key_y=res.key_b,
                                                   results=res.diff)

            if len(res.errors) > 0:
                self.db.insert_batch_diff_error(errors=res.errors)

            # Update the progress
            if self.config.second_loop.use_ram_cache:
                self.block_progress_dict[res.cache_key][res.key_a] = True

        if self.config.second_loop.use_ram_cache:
            self.prune_cache_batch()

    def store_item_second_loop(self, results: List[ItemCompareResult]):
        """
        Store the results of the second loop in the database
        """
        key_success: List[int] = []
        diff_success = []

        errors = {}

        for r in results:
            if r.diff == -1:
                errors[r.key] = r.error
            else:
                key_success.append(r.key)
                diff_success.append(r.diff)

        self.db.insert_batch_diff_item_result(key=key_success, res=diff_success)
        self.db.insert_batch_diff_error(errors=errors)

        if self.config.second_loop.use_ram_cache:
            self.prune_cache_item()
