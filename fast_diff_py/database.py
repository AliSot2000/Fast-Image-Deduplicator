from typing import Union, List

"""
Interface of the Database Class
Serves only as an abstract class. No function of this class will yield any usable result. The default implementation is
the sql_database running on sqlite. For specifics on how to implement the functions for your database, consult the
sql_database.
Inherit from this class and implement all its functions to fully use your own db.
"""


class Database:
    def __init__(self):
        """
        Init function for your implementation of the database
        """
        pass
    # ------------------------------------------------------------------------------------------------------------------
    # CONFIG TABLE
    # ------------------------------------------------------------------------------------------------------------------

    def create_config(self, config: dict, type_name: str) -> bool:
        """
        Create the config table and insert a config dictionary.

        :param config: config dict
        :param type_name: name under which config is stored
        :return: bool -> insert successful or not (key already exists)
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def get_config(self, type_name: str) -> Union[dict, None]:
        """
        Get the config dictionary from the database.

        :param type_name: name under which config is stored
        :return: config dict or None if not found
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def delete_config(self, type_name: str):
        """
        Delete the config from the database.

        :param type_name: name under which config is stored
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def update_config(self, config: dict, type_name: str):
        """
        Update the config to the database.

        :param config: config dict
        :param type_name: name under which config is stored
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def config_table_exists(self):
        """
        Check the master table if the config table exists. DOES NOT VERIFY THE TABLE DEFINITION!

        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    # ------------------------------------------------------------------------------------------------------------------
    # DIRECTORY TABLES
    # ------------------------------------------------------------------------------------------------------------------

    def create_directory_tables(self, purge: bool = True):
        """
        Create the directory tables. Default for purge is true, to recompute it in case the program is stopped during 
        indexing. ASSUMPTION: Indexing is a very fast operation. TODO Handle Stop mid Indexing.

        :param purge: if True, purge the tables before creating them.
        :return:
        """

        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def add_file(self, path: str, filename: str, dir_a: bool = True):
        """
        Add a file to the database.

        :param path: path to file, including filename (e.g. /home/user/file.txt)
        :param filename: filename (e.g. file.txt) // For faster searching
        :param dir_a: if True, add to dir_a, else add to dir_b
        :return:
        """

        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def get_dir_count(self, dir_a: Union[bool, None] = None):
        """
        Get the number of files in the directory table.

        :param dir_a: True, count of dir_a, False, count of dir_b, None count of both.
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def update_dir_success(self, key: int, px: int = -1, py: int = -1):
        """
        Set the flag for success of the file with the matching key. Set it in either table_a or table_b.
        Error not updated.

        :param key: file identifier which is to be updated
        :param px: x count of pixels
        :param py: y count of pixels
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def update_dir_error(self, key: int, msg: str):
        """
        Set the flag for error of the file with the matching key. Set it in either table_a or table_b
        Error is stored in plane text atm (It might be necessary to store it in b64.

        :param key: file identifier which is to be updated
        :param msg: error message created when attempting to process the file.
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def get_next_to_process(self):
        """
        Get an unprocessed entry from the directory table. Returns None per default to signify that there's nothing to
        be computed.

        :return: Next one to compute or None
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def mark_processing(self, task: dict):
        """
        Precondition, the entry already exists, so it can be updated

        :param task: dictionary generated by the get_next_to_process
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def fetch_many_after_key(self, directory_a: bool = True, starting: int = None, count=100) -> List[dict]:
        """
        Fetch count number of rows from a table a or table b starting at a specific key (WHERE key > starting)

        :param directory_a: True use directory_a table else directory_b
        :param starting: select everything with key greater than that
        :param count: number of entries to return
        :return: List[dict] rows wrapped in dict
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def fetch_one_key(self, key: int):
        """
        Fetch exactly the row matching the key and directory.

        :param key: the key of the row
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def insert_hash(self, file_hash: str, key: int, rotation: int):
        """
        Insert a hash into the hash table.

        :param file_hash: hash to insert
        :param key: key of the file in the directory table
        :param rotation: rotation of the file
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def has_all_hashes(self, key: int):
        """
        Check if a file has all hashes populated.

        :param key: key of the file in the directory table
        :return: if a file has all 4 entries.
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def has_any_hash(self, key: int):
        """
        Check if a file has any hash already populated.

        :param key: key of the file in the directory table
        :return: if a file has any entry.
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def del_all_hashes(self, key: int):
        """
        Delete all the 4 possible hashes of a given file.

        :param key: key in the directory table
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def get_hash_of_key(self, key: int) -> list:
        """
        Get the hashes associated with a certain image.

        :param key: the key of the image in the directory table
        :return: [Hash 0, Hash 90, Hash 180, Hash 270]
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def get_many_preprocessing_errors(self, start_key: int = None, count: int = 1000) -> List[dict]:
        """
        Get rows which contain errors. Wrapp the result in dicts and return them.
        
        :param start_key: Starting key.
        :param count: Number of Results to be returned at maximum.
        :return: 
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    # ------------------------------------------------------------------------------------------------------------------
    # THUMBNAIL FILENAME TABLE
    # ------------------------------------------------------------------------------------------------------------------

    def create_thumb_table(self, purge: bool = False):
        """
        Create tables which contain the names of the thumbnails (to make sure there's no collisions ahead of time)

        :param purge: if True, purge the tables before creating them.
        :return:
        """

        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def test_thumb_table_existence(self):
        """
        Check the table for thumbnails of directory table, exists. DOES NOT VERIFY THE TABLE DEFINITION!

        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def get_thumb_name(self, key: int):
        """
        Get the thumbnail name associated with the key.

        :param key: key to search the thumbnail path for
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def generate_new_thumb_name(self, key: int, file_name: str, retry_limit: int = 1000, dir_a: bool = True):
        """
        Generate a new free name for a file. If a file name is taken, will retry a limited number of times again.
        The retry_limit is there to prevent a theoretically endless loop. If this was to trigger for you, update the
        attribute in the FastDifPy class or write your own function.

        :param key: key in the directory_X tables
        :param file_name: file name for which to generate the thumbnail name
        :param retry_limit: how many file names are to be tested.
        :param dir_a: if it is to be inserted into thumb_a or thumb_b table.
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    # ------------------------------------------------------------------------------------------------------------------
    # PLOT TABLE
    # ------------------------------------------------------------------------------------------------------------------

    def create_plot_table(self, purge: bool = False):
        """
        Create tables which contain the filenames of the plots (to make sure there's no collisions ahead of time)

        :param purge: if True, purge the tables before creating them.
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def get_plot_name(self, key_a: int, key_b: int):
        """
        Get the plot name associated with the two keys.

        :param key_a: the first key provided to the dif table.
        :param key_b: the second key provided in the dif table.
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def make_plot_name(self, key_a: int, key_b: int) -> str:
        """
        Generate a new free name for a file. Does not attempt to retry the filename.

        :param key_a: first key provided in the dif table
        :param key_b: second key provided in the dif table
        :return: filename associated with the two keys.
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def get_associated_keys(self, file_name: str) -> Union[tuple, None]:
        """
        Given a file name returns the associated keys in to said plot (if not apparent by the filename I need to put
        in the titles.

        :param file_name: File name to get the two keys from.
        :return: row (tuple) or None
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    # ------------------------------------------------------------------------------------------------------------------
    # HASH TABLE
    # ------------------------------------------------------------------------------------------------------------------

    def create_hash_table(self, purge: bool = False):
        """
        Create the hash table and purge preexisting table if desirerd.

        :param purge: if True, purge the table before creating it.
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    # ------------------------------------------------------------------------------------------------------------------
    # ERROR TABLE
    # ------------------------------------------------------------------------------------------------------------------

    def create_dif_table(self, purge: bool = False):
        """
        Create the dif table. If purge is true, drop a preexisting dif table.

        :param purge: if True, purge the table before creating it.
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")


    def insert_dif_success(self, key_a: int, key_b: int, dif: float) -> bool:
        """
        Insert a new row into the database. If the value exists already, return False, else return True

        :param key_a: key of first image in directory_X table
        :param key_b: key of second image in directory_X table
        :param dif: difference between the images.
        :return: bool if the insert was successful or the key pair existed already.
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def insert_dif_error(self, key_a: int, key_b: int, error: str) -> bool:
        """
        Insert a new row into the database. If the value exists already, return False, else return True

        :param key_a: key of first image in directory_X table
        :param key_b: key of second image in directory_X table
        :param error: error that occurred during processing.
        :return: bool if the insert was successful or the key pair existed already.
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def get_by_pair(self, key_a: int, key_b: int):
        """
        Get the row matching the pair of keys. Return the row wrapped in a dict or None if it doesn't exist.

        :param key_a: key of first image in directory_X table
        :param key_b: key of second image in directory_X table
        :return: None, nothing exists, dict of matching row
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def get_by_table_key(self, key: int):
        """
        Get a row by the table key. Return the row wrapped in a dict tor None if it doesn't exist.

        :param key: unique key in the dif table.
        :return: None, nothing exists, dict of matching row.
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def update_pair_row(self, key_a: int, key_b: int, dif: float = None) -> bool:
        """
        Updates a pair with the new data. if the data is not specified, the preexisting data is used.
        Return true if the update was successful. Return False if the row didn't exist.

        :param key_a: key of first image in directory_X table
        :param key_b: key of second image in directory_X table
        :param dif: difference measurement
        :return: if update was successful
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def get_all_matching_pairs(self, threshold: float):
        """
        Fetches all pairs in the dif table matching the threshold and which terminated successfully.

        :param threshold: in avg diff.
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def get_many_pairs(self, threshold: float, start_key: int = None, count: int = 1000):
        """
        Fetch duplicate pairs from the database. If a start is provided, selecting anything going further from there.

        :param count: number of pairs to fetch.
        :param threshold: below what the dif value needs to be.
        :param start_key: larger than that the diff needs to be.
        :return: tuples from
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def get_many_diff_errors(self, start_key: int = None, count: int = 1000) -> List[dict]:
        """
        Get rows which contain errors. Wrapp the result in dicts and return them.

        :param start_key: Starting key.
        :param count: Number of Results to be returned at maximum.
        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def commit(self):
        """
        Commit any not stored changes now to the filesystem.

        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def disconnect(self):
        """
        Remove the connection to the Database so it can be cleared / deleted.

        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")

    def free(self):
        """
        Removes the database from the filesystem.

        :return:
        """
        raise NotImplementedError("This is only an abstract class ment to layout the signatures.")
