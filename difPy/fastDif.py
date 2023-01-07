from database import Database
import os
from typing import List
import warnings
from utils import *
import multiprocessing as mp
import queue
from datatransfer import *
from image_processor import ImageProcessing

"""
Fast implementation of the DifPy Library.
Features:
- Use GPU to accelerate the comparison
- Use Parallelization to use multicore CPUs
- Use of aspect rotation to ignore images with non-matching aspect ratio
- Use hash based deduplication to find duplicates with color grading
- Use of binary differentiation to detect hard file duplicates  # TODO for later
- Use of file names / zero difference to detect images which differ only in the metadata.  # TODO for later
"""

# TODO test cuda functionality
# TODO create unified handler for the parallel functions
# TODO Make a skeleton for the parallel process
# TODO Test the whole shit.
# TODO Implement process stop recovery.
# TODO single processing handler
# TODO add handler in main function for second foor loop
# TODO Harakiri method. More reckless method.
# TODO Reset Processing Class if the arguments are switched.


def cpu_process_image(proc: ImageProcessing, args: PreprocessArguments) -> PreprocessResults:
    """
    Function to execute preprocessing with the ImageProcessing Class
    # TODO WARNING in docs that an error in the course of processing if it was not fatal, will be treated as fatal.

    :param proc: the ImageProcessing class to retain information (not really necessary currently)
    :param args: arguments loaded from the queue
    :return: PreprocessingResult
    """
    # update arguments and load image
    proc.update_preprocess_args(args=args)

    # check for error
    if proc.error != "":
        return proc.create_error_preprocess_result()

    # return here if only the aspect ratio is desired.
    if not args.compute_hash and not args.store_thumb:
        return proc.create_no_hash_preprocess_result()

    # storing image if desired.
    if args.store_thumb:
        proc.store_image(img_a=True)

    # for safety, returning with error here. It could be possible to continue even if an error
    # occurred while storing the file.
    if proc.error != "":
        return proc.create_error_preprocess_result()

    # return if no hash is desired.
    if not args.compute_hash:
        return proc.create_no_hash_preprocess_result()

    # compute hash
    proc.compute_img_hashes(img_a=True)

    # check for errors again
    if proc.error != "":
        return proc.create_error_preprocess_result()

    # full result return
    return proc.create_full_preprocess_result()


    def load_image(self, image_a: bool = True, perform_resize: bool = True):
        """
        Load image from file_system
        :param image_a: if the image_a should be loaded or image_b
        :param perform_resize: automatically resize image if they don't match the size.
        :return:
        """
        source = "image_a" if image_a else "image_b"
        image_path = self.image_b_path
        thumbnail_path = self.thumb_b_path

        # load the image_a stuff if the image_a is set.
        if image_a:
            image_path = self.image_a_path
            thumbnail_path = self.thumb_a_path

        if thumbnail_path is not None and os.path.exists(thumbnail_path):
            result, err_str, rescale = self.__image_loader(thumbnail_path, f"{source} thumbnail")

            # The thumbnail size matches and no error occurred while loading it. Storing the result and returning.
            if not rescale and err_str == "":
                if image_a:
                    self.image_a_matrix = result
                else:
                    self.image_b_matrix = result
                return

            if err_str != "":
                print(f"{self.identifier: 02}: {err_str}")

        # At this point thumbnail loading failed or the thumbnail was not computed. Load the image and resize if given
        # by args.
        if not os.path.exists(image_path):
            # the main image is the last solution, and we will store the error and return immediately if the error is
            # found.
            self.error = f"Error {source} failed to load because the file does not exist."
            return

        # load the image and return immediately if an error occurred.
        result, err_str, rescale = self.__image_loader(image_path, f"{source} original")
        if err_str != "":
            self.error = err_str
            return

        if image_a:
            self.image_a_matrix = result
        else:
            self.image_b_matrix = result

        # return of we are not allowed to resize, or we don't need to.
        if not perform_resize or not rescale:
            return

        # resize the image
        self.resize_image(image_a)

    def __image_loader(self, img_path: str, source: str) -> Tuple[Union[np.ndarray, None], str, bool]:
        """
        Private function that handles the basic loading and type conversion for an image. Errors are returned as a
        result and not stored in the class because if you load thumbnails, the error has not to be as severe as to
        prevent the computation from completing. That's why it's returned and the caller needs to detmine what
        happens with the error.

        :param img_path: path to load from.
        :param source: the source of the image. Used for the error message
        :return:
        """
        # load from fs
        try:
            img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception as e:
            err_str = f"Error {source} failed to load with:\n {e}"
            return None, err_str, False

        # assert type
        if type(img) != np.ndarray:
            err_str = f"Error {source} failed to load because the image is not of type np.ndarray"
            return None, err_str, False

        # convert grayscale to rgb
        if len(img.shape) == 2:
            img = skimage.color.gray2rgb(img)

        # assert length of image
        img = img[..., 0:3]

        # if one aspect is not matching, rescale the image
        scale = img.shape[0] != self.size_x or img.shape[1] != self.size_y
        return img, "", scale

    def resize_image(self, image_a: bool = True):
        """
        Resize image to image_size provided in the CompareImageArguments object.
        :param image_a: if the image_a should be resized or image_b
        :return:
        """
        img_str = "image_a" if image_a else "image_b"
        try:
            if image_a:
                self.image_a_matrix = cv2.resize(self.image_a_matrix, dsize=(self.size_x, self.size_y),
                                                 interpolation=cv2.INTER_CUBIC)
            else:
                self.image_b_matrix = cv2.resize(self.image_b_matrix, dsize=(self.size_x, self.size_y),
                                                 interpolation=cv2.INTER_CUBIC)
        except Exception as e:
            self.error = f"Error resizing {img_str} failed with:\n {e}"

    def img_rot(self, img_a: bool = True):
        """
        Rotate image by 90 degrees.
        :param img_a: if the image_a should be rotated or image_b
        :return:
        """
        img_str = "image_a" if img_a else "image_b"
        try:
            if img_a:
                self.image_a_matrix = np.rot90(self.image_a_matrix, k=1, axes=(0, 1))
            else:
                self.image_b_matrix = np.rot90(self.image_b_matrix, k=1, axes=(0, 1))
        except Exception as e:
            self.error = f"Error rotating {img_str} failed with:\n {e}"

    def compare_images(self):
        """
        Compare the images and store the result in the class.
        :return:
        """
        # check if the images are loaded
        if self.image_a_matrix is None:
            self.error = "Error image_a is not loaded."
            return
        if self.image_b_matrix is None:
            self.error = "Error image_b is not loaded."
            return

        # compare 0 degrees
        self.diff_0 = self.compare_func(self.image_a_matrix, self.image_b_matrix)

        # compare 90 degrees
        self.img_rot(True)
        self.diff_90 = self.compare_func(self.image_a_matrix, self.image_b_matrix)

        # compare 180 degrees
        self.img_rot(True)
        self.diff_180 = self.compare_func(self.image_a_matrix, self.image_b_matrix)

        # compare 270 degrees
        self.img_rot(True)
        self.diff_270 = self.compare_func(self.image_a_matrix, self.image_b_matrix)

        # rotate back for reuse.
        self.img_rot(True)

    def create_compare_result(self):
        """
        Create a CompareImageResult object from the class.
        :return:
        """
        min_diff = min(self.diff_0, self.diff_90, self.diff_180, self.diff_270)
        return CompareImageResults(key_a=self.processing_args.key_a,
                                   key_b=self.processing_args.key_b,
                                   error=self.error,
                                   success=self.error == "",
                                   min_avg_diff=min_diff)

    def store_plt_on_threashold(self):
        """
        Shorthand to store the plot if the threshold is reached and the storing of the plot is desired.
        :return:
        """
        min_diff = min(self.diff_0, self.diff_90, self.diff_180, self.diff_270)
        if self.processing_args.store_compare and self.processing_args.compare_threshold < min_diff:
            self.store_plt()

    def create_compare_plot(self):
        """
        Create a plot of the two images that are deemed to be similar and store it in predifined path.
        :return:
        """
        fig = plt.figure()
        min_diff = min(self.diff_0, self.diff_90, self.diff_180, self.diff_270)
        plt.suptitle("MSE: %.2f" % (min_diff))

        # plot first image
        ax = fig.add_subplot(1, 2, 1)
        plt.imshow(self.image_a_matrix, cmap=plt.cm.gray)
        plt.axis("off")

        # plot second image
        ax = fig.add_subplot(1, 2, 2)
        plt.imshow(self.image_b_matrix, cmap=plt.cm.gray)
        plt.axis("off")

        plt.show(block=False)
        # show the images
        plt.savefig(self.processing_args.store_path)
        plt.close()


def process_image_cuda(args: PreprocessArguments) -> PreprocessResults:
    # import cupy and use cupy instead of the
    # TODO Test if the process works correctly with cupy
    import cupy as cp

    return process_image(args, xp=cp)


def parallel_resize(iq: mp.Queue, output: mp.Queue, identifier: int, try_cupy: bool) -> bool:
    """
    Parallel implementation of first loop iteration.

    :param iq: input queue containing arguments dict or
    :param output: output queue containing only json strings of obj
    :param identifier: id of running thread
    :param try_cupy: check if cupy is available and use cupy instead.
    :return: True, running was successful and no error encountered, otherwise exit without return or return False
    """
    timeout = 0

    # try to use cupy if it is indicated by arguments
    cupy_avail = False
    if try_cupy:
        print("Cupy version currenetly not implemented")

    img_proc = ImageProcessing(identifier=identifier)

    # stay awake for 60s, otherwise kill
    while timeout < 60:
        try:
            args_str = iq.get(timeout=1)
        except queue.Empty:
            timeout += 1
            continue

        if args_str is None:
            print(f"{identifier:03} Terminating")
            break

        args = PreprocessArguments.from_json(args_str)
        timeout = 0

        result = cpu_process_image(img_proc, args)
        print(f"{identifier:03}: Done with {os.path.basename(args.in_path)}")

        # Sending the result to the handler
        output.put(result.to_json())

    return True


def paralell_compare(in_q: mp.Queue, out_q: mp.Queue, identifier: int, try_cupy: bool) -> bool:
    """
    Parallel implementation of first loop iteration.

    :param in_q: input queue containing arguments dict or
    :param out_q: output queue containing only json strings of obj
    :param identifier: id of running thread
    :param try_cupy: check if cupy is available and use cupy instead.
    :return: True, running was successful and no error encountered, otherwise exit without return or return False
    """
    timeout = 0

    # try to use cupy if it is indicated by arguments
    cupy_avail = False
    if try_cupy:
        try:
            import cupy
            cupy_avail = True
        except ImportError:
            pass

    # stay awake for 60s, otherwise kill
    while timeout < 60:
        try:
            args_str = in_q.get(timeout=1)
        except queue.Empty:
            timeout += 1
            continue

        if args_str is None:
            print(f"{identifier:03} Terminating")
            break

        args = CompareImageArguments.from_json(args_str)
        timeout = 0

        if cupy_avail:
            result = compare_images_cuda(args)
        else:
            result = compare_images(args)
        print(f"{identifier:03}: Done with {os.path.basename(args.in_path)}")

        # Sending the result to the handler
        out_q.put(result.to_json())

    return True


class FastDifPy:
    p_db: str
    __p_root_dir_a: str
    __p_root_dir_b: Union[str, None]

    __thumb_dir_a: str
    __thumb_dir_b: Union[str, None]

    __thumbnail_size_x = 64
    __thumbnail_size_y = 64

    supported_file_types = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif", ".webp"}

    db: Database

    def __init__(self, directory_a: str, directory_b: str = None, test_db: bool = True):
        """
        Provide the directories to be searched. If a different implementation of the database is used,
        set the test_db to false.

        :param directory_a: first directory to search for differentiation.
        :param directory_b: second directory to compare against. Otherwise, comparison will be done against directory
        itself.
        :param test_db: Weather or not the code should test for the presence of the default sqlite database.
        """

        if not os.path.isdir(directory_a):
            raise NotADirectoryError(f"{directory_a} is not a directory")

        if directory_b is not None and not os.path.isdir(directory_b):
            raise NotADirectoryError(f"{directory_b} is not a directory")

        directory_a = os.path.abspath(directory_a)
        directory_b = os.path.abspath(directory_b) if directory_b is not None else None

        # make sure the paths aren't subdirs of each other.
        if directory_b is not None:
            temp_a = directory_a + os.sep
            temp_b = directory_b + os.sep
            if temp_a.startswith(temp_b):
                raise ValueError(f"{directory_a} is a subdirectory of {directory_b}")
            elif temp_b.startswith(temp_a):
                raise ValueError(f"{directory_b} is a subdirectory of {directory_a}")

        self.p_root_dir_b = directory_a
        self.p_root_dir_b = directory_b

        # proceed with the database if the default is used.
        if test_db:
            if not self.test_for_db():
                print("No matching database found. Creating new one.")
                self.db = Database(os.path.join(self.p_root_dir_a, "diff.db"))
                self.write_config()

    def test_for_db(self):
        """
        Test if the database is present in the current directory/ies. Directory A has priority.
        If a Database is found, the config is checked to make sure the paths match the current ones.

        :return: True -> Database connected and ready to use. False -> Database not found.
        """
        db_a = os.path.join(self.p_root_dir_a, "diff.db")
        dir_b = self.p_root_dir_b  # can be none
        db_b = os.path.join(dir_b, "diff.db")
        matching_config = False

        if os.path.exists(db_a):
            temp_db = Database(db_a)
            cfg = temp_db.get_config('main_config')

            # verify the config matches the call arguments (in case the computation was stopped during the
            # execution before)
            if cfg is not None:
                matching_config = cfg["directory_a"] == self.p_root_dir_a and cfg["directory_b"] == self.p_root_dir_b

                # return straight away in case the other directory is not set
                if matching_config and dir_b is None:
                    self.db = temp_db
                    return True

        if dir_b is not None and os.path.exists(db_b):
            temp_db = Database(db_b)
            cfg = temp_db.get_config('main_config')

            # verify the config matches the call arguments (in case the computation was stopped during the
            # execution before)
            if cfg is not None:
                temp_match = cfg["directory_a"] == self.p_root_dir_a and cfg["directory_b"] == self.p_root_dir_b

                if matching_config and temp_match:
                    raise Exception("Two matching configs found. Please remove one of the databases in one of the "
                                    "selected directories so the program can continue.")

                if temp_match:
                    self.db = temp_db
                    return True

        return False

    def get_progress_from_db(self):
        """
        Loads the progress state from the database.
        WARNING: The programm WILL NOT reindex the files. If you added files in the meantime, the files are NOT going
        compared against!
        :return:
        """

        # TODO get the progress from the database
        print("Not implemented yet")

    def write_config(self):
        """
        Write the initial config to the database.
        :return:
        """
        temp_config = {
            "directory_a": self.p_root_dir_a,
            "directory_b": self.p_root_dir_b
        }
        self.db.create_config(type_name="main_config", config=temp_config)

    def index_the_dirs(self):
        """
        List all the files in directory_a and possibly directory_b and store the paths and filenames in the temporary
        database.
        :return:
        """
        # create the tables in the database
        self.db.create_directory_tables(secondary_folder=self.p_root_dir_b is not None)

        self.recursive_index(True)
        if self.p_root_dir_b is not None:
            self.recursive_index(False)

    def recursive_index(self, dir_a: bool = True, path: str = None, ignore_thumbnail: bool = True):
        """
        Recursively index the directories. This function is called by the index_the_dirs function.
        :param ignore_thumbnail: If any directory at any level, starting with .temp_thumb should be ignored.
        :param dir_a: True -> Index dir A. False -> Index dir B
        :param path: The path to the current directory. This is used for recursion.
        :return:
        """

        # load the path to index from
        if path is None:
            if dir_a:
                path = self.p_root_dir_a
            else:
                path = self.p_root_dir_b

        for file_name in os.listdir(path):
            full_path = os.path.join(path, file_name)

            # Thumbnail directory is called .temp_thumbnails
            if file_name.startswith(".temp_thumb") and ignore_thumbnail:
                continue

            # for directories, continue the recursion
            if os.path.isdir(full_path):
                self.recursive_index(dir_a, full_path)

            if os.path.isfile(full_path):
                # check if the file is supported, then add it to the database
                if os.path.splitext(full_path)[1] in self.supported_file_types:
                    self.db.add_file(full_path, file_name, dir_a)

    def estimate_disk_usage(self):
        """
        Estimate the diskusage of the thumbnail directory given the compressed image size.
        :return:
        """
        dir_a_count = self.db.get_dir_count(True)
        dir_b_count = self.db.get_dir_count(False)

        byte_count_a = dir_a_count * self.__thumbnail_size_x * self.__thumbnail_size_y * 3
        byte_count_b = dir_b_count * self.__thumbnail_size_x * self.__thumbnail_size_y * 3

        target = max(len(self.p_root_dir_a), len(self.p_root_dir_b), len(self.p_root_dir_b) + len(self.p_root_dir_a))

        print(
            f"Estimated disk usage by {fill(str(len(self.p_root_dir_a)), target)}: " + h(byte_count_a, "B") + " bytes"
        )
        print(
            f"Estimated disk usage by {fill(str(len(self.p_root_dir_b)), target)}: " + h(byte_count_b, "B") + " bytes"
        )
        print(f"Estimated disk usage by {fill('the two dirs ', target)}: " + h(byte_count_b + byte_count_a,
                                                                               "B") + "bytes")

    def check_create_thumbnail_dir(self):
        """
        Create the thumbnail directories if they don't exist already.
        :return:
        """
        if not os.path.exists(self.thumb_dir_a):
            os.makedirs(self.thumb_dir_a)

        if self.thumb_dir_b is not None and not os.path.exists(self.__thumb_dir_b):
            os.makedirs(self.thumb_dir_b)

    def generate_thumbnail_path(self, key: int, filename: str, dir_a: bool):
        """
        Generate the thumbnail_path first tries to fetch the thumbnail name from the db if it exists already,
        otherwise generate a new name.

        :param key: key in the directory_x table
        :param filename: the name of the file with extension
        :param dir_a: if the file is located in directory a or b
        :return: the thumbnail path.
        """
        name = self.db.get_thumb_name(key, dir_a=dir_a)
        directory = self.thumb_dir_a if dir_a else self.__thumb_dir_b

        # return the name if it existed already
        if name is not None:
            return os.path.join(directory, name)

        name = self.db.generate_new_thumb_name(key, filename, dir_a=dir_a)
        return os.path.join(directory, name)

    def first_loop_iteration(self, compute_thumbnails: bool = True, compute_hash: bool = False, amount: int = 4,
                             gpu_proc: int = 0, cpu_proc: int = 16):

        # TODO MAKE EVERYTHING WITH ProcesspoolExecutor
        # store thumbnails if possible.
        if compute_hash:
            if amount == 0:
                print("WARNING: amount 0, only EXACT duplicates are detected like this.")

            if amount > 7 or amount < -7:
                raise ValueError("amount my only be in range [-7, 7]")

            self.db.create_hash_table()

        # thumbnail are required to exist for both.
        if compute_thumbnails or compute_hash:
            self.check_create_thumbnail_dir()

        cpu_handles = []
        gpu_handles = []

        task_queue = mp.Queue(maxsize=(cpu_proc + gpu_proc) * 2)
        res_queue = mp.Queue()

        # prefill loop
        for i in range(cpu_proc + gpu_proc):
            task = self.db.get_next_to_process()

            # stop if there's nothing left to do.
            if task is None:
                break

            # generate a new argument
            arg = PreprocessArguments(
                amount=amount,
                key=task["key"],
                in_path=task["path"],
                out_path=self.generate_thumbnail_path(dir_a=task["dir_a"], filename=task["filename"], key=task["key"]),
                compute_hash=compute_hash,
                store_thumb=compute_thumbnails,
                size_x=self.thumbnail_size_x,
                size_y=self.thumbnail_size_y,
            )

            task_queue.put(arg.to_json())

        # start processes for cpu
        for i in range(cpu_proc):
            p = mp.Process(target=parallel_resize, args=(task_queue, res_queue, i, False))
            p.start()
            cpu_handles.append(p)

        # start processes for gpu
        for i in range(cpu_proc, gpu_proc + cpu_proc):
            p = mp.Process(target=parallel_resize, args=(task_queue, res_queue, i, True))
            p.start()
            gpu_handles.append(p)

        # turn main loop into handler and perform monitoring of the threads.
        run = True
        none_counter = 0
        timeout = 0

        # handle the running state of the loop
        while run:
            if self.handle_result_of_first_loop(res_queue, compute_hash):
                task = self.db.get_next_to_process()

                # if there's no task left, stop the loop.
                if task is None:
                    none_counter += 1
                    task_queue.put(None)

                else:
                    # generate a new argument
                    arg = PreprocessArguments(
                        amount=amount,
                        key=task["key"],
                        in_path=task["path"],
                        out_path=self.generate_thumbnail_path(dir_a=task["dir_a"], filename=task["filename"],
                                                              key=task["key"]),
                        compute_hash=compute_hash,
                        store_thumb=compute_thumbnails,
                        size_x=self.thumbnail_size_x,
                        size_y=self.thumbnail_size_y,
                    )

                    task_queue.put(arg.to_json())
                    timeout = 0
            else:
                timeout += 1

            # if this point is reached, all processes should be done and the queues empty.
            if none_counter >= cpu_proc + gpu_proc:
                run = False

            # at this point we should have been idling for 60s
            if timeout > 60:
                print("Timeout reached, stopping.")
                run = False

        # adding Nones just for good measure.
        counter = 0
        while not task_queue.full() and counter < 1000:
            task_queue.put(None)
            counter += 1

        # all processes should be done now, iterating through and killing them if they're still alive.
        for i in range(len(cpu_handles)):
            p = cpu_handles[i]
            try:
                print(f"Trying to join process {i} Process State is {p.is_alive()}")
                p.join(60)
            except TimeoutError:
                print(f"Process {i} timed out. Alive state: {p.is_alive()}; killing it.")
                p.kill()

        for i in range(len(gpu_handles)):
            p = gpu_handles[i]
            try:
                print(f"Trying to join process {i + cpu_proc} Process State is {p.is_alive()}")
                p.join(60)
            except TimeoutError:
                print(f"Process {i + cpu_proc} timed out. Alive state: {p.is_alive()}; killing it.")
                p.kill()

        # try to handle any remaining results that are in the queue.
        for _ in range((cpu_proc + gpu_proc) * 2):
            if not self.handle_result_of_first_loop(res_queue, compute_hash):
                break

        assert res_queue.empty(), "Result queue is not empty after all processes have been killed."
        print("All Images have been preprocessed.")

    def handle_result_of_first_loop(self, res_q: mp.Queue, compute_hash: bool) -> bool:
        """
        Dequeues a result of the results queue and updates the database accordingly.
        :param res_q: results queue
        :param compute_hash: if the hash was computed
        :return: if a result was handled.
        """
        # retrieve the result from the queue
        try:
            res = res_q.get(timeout=1)
        except queue.Empty:
            return False

        # sanitize result
        assert type(res) is str, "Result is not a string"
        result_obj = PreprocessResults.from_json(res)

        # Handle the case when an error occurred.
        if not result_obj.success:
            self.db.update_dir_error(key=result_obj.key, dir_a=result_obj.dir_a, msg=result_obj.error)
            return True

        # store the hash if computed
        if compute_hash:
            # Drop hashes if they are only partly computed.
            if not self.db.has_all_hashes(dir_a=result_obj.dir_a, dir_key=result_obj.key):
                self.db.del_all_hashes(dir_a=result_obj.dir_a, dir_key=result_obj.key)

            # Store all hashes
            self.db.insert_hash(dir_a=result_obj.dir_a, dir_key=result_obj.key, fhash=result_obj.hash_0, rotation=0)
            self.db.insert_hash(dir_a=result_obj.dir_a, dir_key=result_obj.key, fhash=result_obj.hash_90, rotation=90)
            self.db.insert_hash(dir_a=result_obj.dir_a, dir_key=result_obj.key, fhash=result_obj.hash_180, rotation=180)
            self.db.insert_hash(dir_a=result_obj.dir_a, dir_key=result_obj.key, fhash=result_obj.hash_270, rotation=270)

        # mark file as processed only if the other data was inserted.
        self.db.update_dir_success(key=result_obj.key, dir_a=result_obj.dir_a, px=result_obj.original_x,
                                   py=result_obj.original_y)

        # to be sure commit here.
        self.db.con.commit()
        return True

    def clean_up(self):
        # TODO remove the thumbnails
        # TODO remove database (if desired)
        print("Not implemented yet")

    # ------------------------------------------------------------------------------------------------------------------
    # PROPERTIES
    # ------------------------------------------------------------------------------------------------------------------

    @property
    def thumbnail_size_x(self):
        return self.__thumbnail_size_x

    @thumbnail_size_x.setter
    def thumbnail_size_x(self, value):
        if value < 0:
            raise ValueError("Thumbnail size must be positive")

        if value > 1000:
            warnings.warn("Thumbnail size is very large. Higher Accuracy will slow down the process and "
                          "increase storage usage.")
        self.__thumbnail_size_x = value

    @property
    def thumbnail_size_y(self):
        return self.__thumbnail_size_y

    @thumbnail_size_y.setter
    def thumbnail_size_y(self, value):
        if value < 0:
            raise ValueError("Thumbnail size must be positive")

        if value > 1000:
            warnings.warn("Thumbnail size is very large. Higher Accuracy will slow down the process and "
                          "increase storage usage.")
        self.__thumbnail_size_y = value

    @property
    def p_root_dir_a(self):
        return self.__p_root_dir_a

    @p_root_dir_a.setter
    def p_root_dir_a(self, value):
        if os.path.exists(value):
            self.__p_root_dir_a = value
            self.__thumb_dir_a = os.path.join(self.__p_root_dir_a, ".temp_thumbnails")

    @property
    def p_root_dir_b(self):
        return self.__p_root_dir_b

    @p_root_dir_b.setter
    def p_root_dir_b(self, value):
        if os.path.exists(value):
            self.__p_root_dir_b = value
            self.__thumb_dir_b = os.path.join(self.__p_root_dir_b, ".temp_thumbnails")

    @property
    def thumb_dir_a(self):
        return self.__thumb_dir_a

    @property
    def thumb_dir_b(self):
        return self.__thumb_dir_b
