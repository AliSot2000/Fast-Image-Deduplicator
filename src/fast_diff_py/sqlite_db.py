import os.path
import shutil
from typing import List, Dict, Set, Tuple, Union

from fast_diff_py.datatransfer_new import PreprocessArg, PreprocessResult
from fast_diff_py.sqlite_wrapper import BaseSQliteDB
from fast_diff_py.utils import to_b64


class SQLiteDB(BaseSQliteDB):
    debug: bool
    def __init__(self, db_path: str, debug: bool = False):
        """
        In Debug Mode, Model Validation is turned on, for performance reasons, it's skipped.
        """
        super().__init__(db_path)
        self.debug = debug

    @staticmethod
    def __get_directory_table_names(temp: bool = False) -> str:
        """
        Get the table names for the directories

        :param temp: Whether to get the temp table or not
        """
        if temp:
            tbl_name = "directory_temp"
        else:
            tbl_name = "directory"

        return tbl_name

    # Create Tables Command
    def create_directory_table_and_index(self, temp: bool = False):
        """
        Create the table for the directories
        :param temp: Whether to create the temp table or not
        """
        tbl_name = self.__get_directory_table_names(temp)

        stmt = (f"CREATE TABLE {tbl_name} ("
                f"key INTEGER PRIMARY KEY AUTOINCREMENT, "
                f"path TEXT, "
                f"filename TEXT, "
                f"error TEXT, "
                f"success INTEGER DEFAULT -1 CHECK ({tbl_name}.success IN (-2, -1, 0, 1)), "
                f"px INTEGER DEFAULT -1 CHECK ({tbl_name}.px >= -1), "
                f"py INTEGER DEFAULT -1 CHECK ({tbl_name}.py >= -1), "
                f"dir_b INTEGER DEFAULT 0 CHECK ({tbl_name}.dir_b IN (0, 1)), "
                f"hash_0 INTEGER, "
                f"hash_90 INTEGER, "
                f"hash_180 INTEGER, "
                f"hash_270 INTEGER,  "
                f"UNIQUE (path, dir_b))")

        self.debug_execute(stmt, )
        if not temp:
            self.create_directory_indexes()

    def create_directory_indexes(self):
        """
        Create the indexes on the directory table
        """
        self.debug_execute(f"CREATE INDEX directory_key_index ON directory (key)")
        self.debug_execute(f"CREATE INDEX directory_dir_b_index ON directory (dir_b)")
        self.debug_execute(f"CREATE INDEX directory_success_index ON directory (success)")

    def drop_directory_index(self):
        """
        Drop the index on the directory table
        """
        self.debug_execute("DROP INDEX IF EXISTS directory_key_index")
        self.debug_execute("DROP INDEX IF EXISTS directory_dir_b_index")
        self.debug_execute("DROP INDEX IF EXISTS directory_success_index")

    def create_hash_table_and_index(self):
        """
        Create the table for the hash values and create an index for faster lookups
        """
        stmt = ("CREATE TABLE hash_table ("
                "key INTEGER PRIMARY KEY AUTOINCREMENT , "
                "hash TEXT UNIQUE , "
                "count INTEGER CHECK (hash_table.count >= 0))"
                )

        self.debug_execute(stmt)

        stmt = "CREATE INDEX hash_table_index ON hash_table (hash)"
        self.debug_execute(stmt)

        self.create_hash_indexes()

    def create_diff_table_and_index(self):
        """
        Create the table for the diffs.
        """
        stmt = ("CREATE TABLE dif_table ("
                "key INTEGER PRIMARY KEY AUTOINCREMENT, "
                "key_a INTEGER NOT NULL, "
                "key_b INTEGER NOT NULL, "
                "dif REAL CHECK (dif_table.dif >= -1) DEFAULT -1, "
                "success INT CHECK (dif_table.success IN (-1, 0, 1, 2, 3)) DEFAULT -1, "
                "block_a INTEGER, "
                "block_b INTEGER, "
                "block_key INTEGER,"
                "UNIQUE (key_a, key_b)) ")

        self.debug_execute(stmt)

        stmt = "CREATE TABLE dif_error_table (key INTEGER PRIMARY KEY, error TEXT)"
        self.debug_execute(stmt)

        self.debug_execute("CREATE INDEX err_table_key ON dif_error_table (key)")

        self.debug_execute("CREATE INDEX dif_table_key_index ON dif_table (key)")
        self.debug_execute("CREATE INDEX dif_table_key_a_key_b_index ON dif_table (key_a, key_b)")
        self.debug_execute("CREATE INDEX dif_table_block_key ON dif_table (block_key)")

    def create_hash_indexes(self):
        """
        Add indexes on hashes for improved performance when retrieving the duplicates based on hash.
        """
        self.debug_execute("CREATE INDEX directory_hash_0_index ON directory (hash_0)")
        self.debug_execute("CREATE INDEX directory_hash_90_index ON directory (hash_90)")
        self.debug_execute("CREATE INDEX directory_hash_180_index ON directory (hash_180)")
        self.debug_execute("CREATE INDEX directory_hash_270_index ON directory (hash_270)")

    # ==================================================================================================================
    # Dir Table
    # ==================================================================================================================

    def bulk_insert_file(self, path: str, filenames: List[str], dir_b: bool = False):
        """
        Insert a folder of files into the database

        :param path: The path to the folder
        :param filenames: The list of filenames
        :param dir_b: Whether this is the B directory or not
        """
        stmt = "INSERT INTO directory (path, filename, dir_b) VALUES (?, ?, ?)"
        _dir_b = 1 if dir_b else 0
        tgt = [(os.path.join(path, f), f, _dir_b) for f in filenames]
        self.debug_execute_many(stmt, tgt)

    def reset_preprocessing(self):
        """
        Reset the Preprocessing flag of -2 in the dir table
        """
        self.debug_execute("UPDATE directory SET success = -1 WHERE success = -2")

    def batch_of_preprocessing_args(self, batch_size: int) -> List[PreprocessArg]:
        """
        Get a batch of preprocessing args

        :param batch_size: How many rows to fetch at once
        """
        stmt = ("SELECT key, path FROM directory WHERE success = -1 LIMIT ?")
        self.debug_execute(stmt, (batch_size,))

        if self.debug:
            results = [PreprocessArg(file_path=row[1], key=row[0]) for row in self.sq_cur.fetchall()]
        else:
            results = [PreprocessArg.model_construct(file_path=row[1], key=row[0]) for row in self.sq_cur.fetchall()]

        # Update to processing
        stmt = ("UPDATE directory SET success = -2 WHERE key IN "
                "(SELECT key FROM directory WHERE success = -1 LIMIT ?)")
        self.debug_execute(stmt, (batch_size,))

        return results

    def batch_of_first_loop_results(self, results: List[PreprocessResult], has_hash: bool = False):
        """
        Insert the results of the preprocessing into the database
        """
        if self.debug:
            for r in results:
                assert r is not None, "Result is None"
        err = []
        success = []

        # Split errors and successes
        for res in results:
            if res.error is not None:
                err.append(res)
            else:
                success.append(res)

        # Update the errors
        update_err = [(to_b64(res.error), 0, res.key) for res in err]
        update_err_stmt = "UPDATE directory SET error = ?, success = ? WHERE key = ?"
        self.debug_execute_many(update_err_stmt, update_err)

        # Update the successes
        if has_hash:
            # Update that has hash
            update_success = [(res.org_x, res.org_y, res.hash_0, res.hash_90, res.hash_180, res.hash_270, res.key)
                              for res in success]
            update_success_stmt = (
                "UPDATE directory SET px = ?, py = ?, hash_0 = ?, hash_90 = ?, hash_180 = ?, hash_270 = ?, success = 1"
                " WHERE key = ?" )

            # Update that doesn't have hash
        else:
            update_success = [(res.org_x, res.org_y, res.key) for res in success]
            update_success_stmt = "UPDATE directory SET px = ?, py = ?, success = 1 WHERE key = ?"

        self.debug_execute_many(update_success_stmt, update_success)

    def get_dir_entry_count(self, dir_b: bool) -> int:
        """
        Get the number of entries in the directory table
        """
        dir_b = 1 if dir_b else 0
        stmt = "SELECT COUNT(*) FROM directory WHERE dir_b = ?"
        self.debug_execute(stmt, (dir_b,))
        return self.sq_cur.fetchone()[0]

    def get_b_offset(self) -> int:
        """
        Get the index belonging to dir_b
        """
        stmt = "SELECT MIN(key) FROM directory WHERE dir_b = 1"
        self.debug_execute(stmt)
        return self.sq_cur.fetchone()[0]

    # INFO: Needs to be called before loop 1
    def set_keys_zero_index(self):
        """
        Update the keys of the directory table such that they start at 0 instead of 1
        """
        self.debug_execute("UPDATE directory SET key = key - (SELECT MIN(key) FROM directory)")

    def swap_dir_b(self):
        """
        Swap the definition of dir_a and dir_b
        Needed for performance improvements
        """
        # Create a temp table
        self.create_directory_table_and_index(True)
        tmp_tbl = self.__get_directory_table_names(True)
        d_tbl = self.__get_directory_table_names(False)

        # Inserting the directory_b entries first
        stmt = (f"INSERT INTO {tmp_tbl} (path, filename, error, success, px, py, dir_b, hash_0, hash_90, hash_180, hash_270)"
                f" SELECT path, filename, error, success, px, py, 0 AS dir_b, hash_0, hash_90, hash_180, hash_270 "
                f"FROM {d_tbl} WHERE dir_b = 1")

        self.debug_execute(stmt)

        stmt = (f"INSERT INTO {tmp_tbl} (path, filename, error, success, px, py, dir_b, hash_0, hash_90, hash_180, hash_270)"
                f" SELECT path, filename, error, success, px, py, 1 AS dir_b, hash_0, hash_90, hash_180, hash_270 "
                f"FROM {d_tbl} WHERE dir_b = 0")

        self.debug_execute(stmt)

        # Dropping old table and index
        # INFO Index is dropped wiht table
        self.debug_execute(f"DROP TABLE {d_tbl}")

        # Renaming the temp table and index
        self.debug_execute(f"ALTER TABLE {tmp_tbl} RENAME TO {d_tbl}")
        self.drop_directory_index()
        self.create_directory_indexes()

    # ==================================================================================================================
    # Hash Table
    # ==================================================================================================================

    def bulk_insert_hashes(self, hashes: List[str]):
        """
        Insert a list of hashes into the hash table. Performs either an insert, if the hash doesn't exist or updates
        the matching hash

        :param hashes: List of hashes to insert
        """
        stmt = "INSERT INTO hash_table (hash, count) VALUES (?, 1) ON CONFLICT(hash) DO UPDATE SET count = count + 1;"
        tgt = [(h,) for h in hashes]
        self.debug_execute_many(stmt, tgt)

    def get_bulk_hash_lookup(self, hashes: Set[str]) -> Dict[str, int]:
        """
        Get the keys for a list of hashes

        :param hashes: List of hashes to lookup

        :return: List of keys
        """
        lookup: Dict[str, int] = {}

        for h in hashes:
            self.debug_execute("SELECT key FROM hash_table WHERE hash = ?", (h,))
            res = self.sq_cur.fetchone()
            assert res is not None, "Hash not found"
            lookup[h] = res[0]

        return lookup

    # TODO Extract duplicates based on hash

    # ==================================================================================================================
    # Diff Table
    # ==================================================================================================================

    def prepopulate_diff_table(self, block_size: int, has_dir_b: bool = False):
        """
        Prepopulate the diff table with the keys

        :param block_size: The block size for cache optimization
        :param has_dir_b: Whether the table has a dir_b column
        """
        # Populate the Table with all diff entries that will exist
        if has_dir_b:
            stmt = ("INSERT INTO dif_table (key_a, key_b) "
                    "SELECT a.key, b.key FROM directory AS a CROSS JOIN directory AS b "
                    "WHERE a.dir_b = 0 AND b.dir_b = 1 "
                    "ORDER BY a.key, b.key")
        else:
            stmt = ("INSERT INTO dif_table (key_a, key_b) "
                    "SELECT a.key, b.key FROM directory AS a CROSS JOIN directory AS b "
                    "WHERE a.key < b.key "
                    "ORDER BY a.key, b.key")

        # Prepopulating the table
        self.debug_execute(stmt)

        # Set the blocks
        if has_dir_b:
            stmt = ("UPDATE dif_table "
                    "SET block_a = key_a / ?, "
                    "block_b = (key_b - (SELECT MIN(key) FROM directory WHERE dir_b = 1)) / ?")

        else:
            stmt = ("UPDATE dif_table "
                    "SET block_a = key_a / ?, "
                    "block_b = key_b / ?")

        # We get the blocks
        self.debug_execute(stmt, (block_size, block_size))

        # -- Convert the blocks to block keys --
        # Drop table if it exists
        self.debug_execute("DROP TABLE IF EXISTS block_key_temp")

        # Create a temporary table
        stmt = ("CREATE TABLE block_key_temp "
                "(key INTEGER PRIMARY KEY AUTOINCREMENT, "
                "block_a INTEGER, "
                "block_b INTEGER, "
                "UNIQUE (block_a, block_b))")
        self.debug_execute(stmt)

        # Insert the block keys
        if has_dir_b:
            # Indexing along x and y
            stmt = ("INSERT INTO block_key_temp (block_a, block_b) "
                    "SELECT DISTINCT block_a, block_b FROM dif_table ORDER BY block_a, block_b;")
        else:
            # Indexing along the diagonals
            stmt = ("INSERT INTO block_key_temp (block_a, block_b) "
                    "SELECT DISTINCT block_a, block_b FROM dif_table ORDER BY (block_b - block_a), (block_b + block_a);")

        self.debug_execute(stmt)

        # Update the block_keys from the temp table
        stmt = ("UPDATE dif_table "
                "SET block_key = (SELECT key FROM block_key_temp "
                "                 WHERE block_a = dif_table.block_a AND block_b = dif_table.block_b);")
        self.debug_execute(stmt)

        # Drop the temporary table
        self.debug_execute("DROP TABLE block_key_temp")

    # TODO:
    #  Add a function which short circuits hash and aspect ratio comparison

    def get_cache_block_thumb(self, block_key: int, has_dir_b: bool = False) \
            -> Tuple[int, int, int, int] :
        """
        For a given block, returns, the start_key in x, the start_key in y and the size of the y-block and x-block
        respectively.

        :param block_key: The block key
        :param has_dir_b: Whether the table has a dir_b column

        :return: Tuple[lower_key_x, lower_key_y, size_x, size_y], (-1 ,-1 ,-1, -1) if the block doesn't exist

        """
        stmt = ("SELECT COUNT(DISTINCT (key_a)), MIN(key_a), COUNT(DISTINCT (key_b)), MIN(key_b) "
                "FROM dif_table WHERE dif_table.block_key = ?; ")
        self.debug_execute(stmt, (block_key,))
        res = self.sq_cur.fetchone()

        if (0, None, 0, None) == res:
            return -1, -1, -1, -1

        size_x, lower_key_x, size_y, lower_key_y = res
        # INFO Breaks the cache pruning
        # if lower_key_x + 1 == lower_key_y and not has_dir_b:
        #     assert size_x == size_y, ("We should be in the diagonal, as such, we're expecting the same size i.e "
        #                               "max batch-size -1 and the two lower_keys should be off by one")
        #     lower_key = lower_key_x
        #     size = size_x + 1
        #     return lower_key, lower_key, size, size

        return lower_key_x, lower_key_y, size_x, size_y

    def get_cache_block_original(self, block_key: int, has_dir_b: bool = False) \
        -> Tuple[int, int, List[str], List[str]]:
        """
        Get the cache block i.e. rows for y and x given no precomputed thumbnails. So the file paths need to be
        provided instead of the keys.

        :param block_key: The block key
        :param has_dir_b: Whether the table has a dir_b column

        :return: Tuple[lower_key_x, lower_key_y, path_batch_x, path_batch_y]
        """
        lower_x, lower_y, size_x, size_y = self.get_cache_block_thumb(block_key, has_dir_b)

        # Exit on empty
        if (lower_x, lower_y, size_x, size_y) == (-1, -1, -1, -1):
            return -1, -1, [], []

        # Get a paths
        self.debug_execute("SELECT path FROM directory WHERE key >= ? LIMIT ?", (lower_x, size_x))
        paths_a = [row[0] for row in self.sq_cur.fetchall()]

        assert len(paths_a) == size_x, "Size of paths_a doesn't match the size of the block"

        # Get b paths
        self.debug_execute("SELECT path FROM directory WHERE key >= ? LIMIT ?", (lower_y, size_y))
        paths_b = [row[0] for row in self.sq_cur.fetchall()]

        assert len(paths_b) == size_y, "Size of paths_b doesn't match the size of the block"

        # INFO Breaks the cache pruning
        # We're on the diagonal, we can use faster computation
        # if lower_x + 1 == lower_y and has_dir_b:
        #     assert set(paths_a + paths_b) == set([paths_a[0]] + paths_b), \
        #         "Paths don't match expectation in case of diagonal"
        #     paths = [paths_a[0]] + paths_b
        #     return lower_x, lower_x, paths, paths

        return lower_x, lower_y, paths_a, paths_b

    def get_task_block_key(self, block_key: int) -> List[Tuple[int, int, int]]:
        """
        Get a block of tasks, defined by:
        - max_key
        - min_key_a
        - max_key_b

        -> Info, size is not necessary. Child can compute them by (max_key_b // block-size) * block-size = min_key_b

        :return: List of tuples with the following information: key, key_a, key_b
        """
        # stmt = ("SELECT key, dif_table.key_a, dif_table.key_b FROM dif_table "
        #         "WHERE dif_table.block_key = ? AND dif_table.key_b - (? - 1) = block_b * ?;")
        stmt = "SELECT key, key_a, MAX(key_b) FROM dif_table WHERE block_key = ? GROUP BY key_a;"
        # self.debug_execute(stmt, (block_key, block_key, block_key))
        self.debug_execute(stmt, (block_key,))
        rows = self.sq_cur.fetchall()
        return rows

    # INFO, we put this function here as a reminder that another function exists.
    def get_task_block_paths(self, block_key):
        """
        Get the paths for a block key
        """
        raise NotImplementedError("This function is not implemented -> use get_cache_block_original instead")

    # TODO update after the child loop implementation
    def insert_batch_diff_block_result(self, min_key_x: int, max_key_y: int, results: List[float]):
        """
        Insert the results of the diff into the database
        """
        args = [(results[i], min_key_x, max_key_y - i) for i in range(len(results))]
        stmt = "UPDATE dif_table SET dif = ?,  success = 1 WHERE key_a = ? AND key_b = ?"
        self.debug_execute_many(stmt, args)

    def insert_batch_diff_item_result(self, key: List[int], res: List[float]):
        """
        Insert the results of the diff into the database
        """
        args = [(res[i], key[i]) for i in range(len(key))]
        stmt = "UPDATE dif_table SET dif = ?, success = 1 WHERE key = ?"
        self.debug_execute_many(stmt, args)

    def insert_batch_diff_error(self, errors: Dict[int, str]):
        """
        Insert the error of the diff into the database
        """
        # Set errors in the dif_table
        args = [(key,) for key, value in errors.items()]
        stmt = "UPDATE dif_table SET success = 0 WHERE key = ?"
        self.debug_execute_many(stmt, args)

        # Insert the errors
        args = [(key, to_b64(value)) for key, value in errors.items()]
        stmt = "INSERT INTO dif_error_table (key, error) VALUES (?, ?)"
        self.debug_execute_many(stmt, args)

    def get_item_block(self, block_key: int, include_block_key: bool = False) -> \
            Union[List[Tuple[int, int, int, str, str, int]], List[Tuple[int, int, int, str, str]]]:
        """
        Get the information for a block of items. So it can be ensured that the cache can be used. If we want to use a
        cache, we need to include the block_key, otherwise we don't need that.

        :param block_key: The block key
        :param include_block_key: Whether to include the block key or not

        :returns List of tuples with the following information:
            - key
            - key_a
            - key_b
            - path_a
            - path_b
            - block_key (if include_block_key is True) otherwise, this is not included
        """
        if include_block_key:
            stmt = ("SELECT d.key, d.key_a, d.key_b, a.path, b.path, d.block_key "
                    "FROM dif_table AS d JOIN directory AS a ON key_a = a.key JOIN directory AS b ON key_b = b.key "
                    "WHERE block_key = ? AND d.success = -1")
        else:
            stmt = ("SELECT d.key, d.key_a, key_b, a.path, b.path "
                    "FROM dif_table AS d JOIN directory AS a ON key_a = a.key JOIN directory AS b ON key_b = b.key "
                    "WHERE block_key = ? AND d.success = -1")

        self.debug_execute(stmt, (block_key,))
        return self.sq_cur.fetchall()

    def verify_item_block(self, block_key: int):
        """
        Make sure all entries in the block have been computed.

        :param block_key: The block key

        :return: True if all entries have been computed, False otherwise
        """
        stmt = "SELECT COUNT(*) FROM dif_table WHERE block_key = ? AND success = -1"
        self.debug_execute(stmt, (block_key,))

        res = self.sq_cur.fetchone()
        assert res is not None, "Block not found"

        return res[0] == 0

    def get_pair_count_diff(self):
        """
        Get the number of pairs that need to be computed
        """
        stmt = "SELECT COUNT(*) FROM dif_table WHERE success = -1"
        self.debug_execute(stmt)
        return self.sq_cur.fetchone()[0]

    def get_duplicate_pairs(self, delta: float) -> List[Tuple[str, str, float]]:
        """
        Get all Pairs of images that are below the threshold from the table.

        :param delta: The threshold for the difference

        :return: List of tuples with the following information:
        - path_a
        - path_b
        - dif
        """
        stmt = ("SELECT a.path, b.path, d.dif "
                "FROM dif_table AS d "
                "JOIN directory AS a ON a.key = d.key_a "
                "JOIN directory AS b ON b.key = d.key_b "
                "WHERE dif < ? AND d.success = 1 ORDER BY d.key_a, d.key_b")

        self.debug_execute(stmt, (delta,))
        for row in self.sq_cur.fetchall():
            yield row

    def get_cluster(self, delta: float, group_a: bool = True) -> Tuple[str, Dict[str, float]]:
        """
        Get clusters of images that have a difference below the threshold.

        :param delta: The threshold for the difference
        :param group_a: Either group by directory a or directory b.
        """
        if group_a:
            stmt = ("SELECT a.path, b.path, d.dif "
                "FROM dif_table AS d "
                "JOIN directory AS a ON a.key = d.key_a "
                "JOIN directory AS b ON b.key=d.key_b "
                "WHERE dif < ? AND d.success = 1 ORDER BY d.key_a, d.key_b")
        else:
            stmt = ("SELECT a.path, b.path, d.dif "
                "FROM dif_table AS d "
                "JOIN directory AS a ON a.key = d.key_a "
                "JOIN directory AS b ON b.key=d.key_b "
                "WHERE dif < ? AND d.success = 1 ORDER BY d.key_b, d.key_a")

        self.debug_execute(stmt, (delta,))

        row = self.sq_cur.fetchone()

        if row is None:
            return -1, {}

        head = row[0] if group_a else row[1]
        acc = [row]

        while True:
            row = self.sq_cur.fetchone()

            # Exit Loop condition
            if row is None:
                if group_a:
                    yield head, {r[1]: r[2] for r in acc}
                else:
                    yield head, {r[0]: r[2] for r in acc}
                break

            # Check the new head
            int_head = row[0] if group_a else row[1]
            if int_head == head:
                acc.append(row)
            else:
                if group_a:
                    yield head, {r[1]: r[2] for r in acc}
                else:
                    yield head, {r[0]: r[2] for r in acc}
                head = int_head
                acc = [row]

    def get_min_cache_key(self):
        """
        For a restart of the second loop, get the cache key that is the smallest and
        doesn't have all the values filled in
        """
        self.debug_execute("SELECT MIN(batch_key) FROM cache_table WHERE success = -1")
        return self.sq_cur.fetchone()[0]

    def drop_diff(self, threshold: float):
        """
        Drop all diffs above a certain threshold
        """
        self.debug_execute("DELETE FROM dif_table WHERE dif > ?", (threshold,))

    def make_backup(self, backup_path: str):
        """
        Make a backup of the database
        """
        self.commit()
        self.close()
        shutil.copy(self.db_path, backup_path)
        self.connect(self.db_path)