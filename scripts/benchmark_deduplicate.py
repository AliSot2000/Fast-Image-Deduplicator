import difPy.dif as difpy
import fast_diff_py as fast_diff
import datetime
import json
import argparse
from typing import List
import os
import subprocess
import pickle
import shutil

from fast_diff_py import FastDifPy

"""
This file is used to perform the benchmark of the all to all comparison step.

For difpy, we're performing the action of the "search" class. For FastDiffPy, we're performing the action of second loop
"""

# Defaults
number_of_processes = [4, 8, 16]
retries = 3
target_size = 64


def difpy_preamble(directory: List[str], size: int, file: str):
    """
    Perform the setup step of difpy
    """
    if len(directory) == 0:
        raise ValueError("Directory cannot be empty")

    dif = difpy.build(*directory, px_size=size)

    # Writing dif build object to file for recovery
    with open(file, "wb") as f:
        pickle.dump(dif, f)

    del dif


def fast_diff_preamble(directory: List[str], size: int, task_dir: str, rotate: bool):
    """
    Perform the setup step of fast_diff
    """
    if len(directory) == 0:
        raise ValueError("Directory cannot be empty")

    if len(directory) == 1:
        part_a = directory[0]
        part_b = None
    else:
        part_b = directory[:-1]
        part_a = directory[-1]

    fdo = FastDifPy(part_a=part_a, part_b=part_b, purge=True, compression_target=size, rotate=rotate,
                    thumb_dir=os.path.join(task_dir, FastDifPy.default_thumb_dir),
                    db_path=os.path.join(task_dir, FastDifPy.default_db_file),
                    config_path=os.path.join(task_dir, FastDifPy.default_config_file))
    fdo.full_index()
    fdo.first_loop(fast_diff.FirstLoopConfig(compute_hash=True, shift_amount=0))
    fdo.commit()
    fdo.config.delete_db = False
    fdo.config.delete_thumb = False
    fdo.config.retain_progress = True
    fdo.cleanup()
    del fdo


def difpy_epilogue(file: str):
    """
    Remove the dif object we wrote to disk
    """
    if os.path.exists(file):
        os.remove(file)


def fast_diff_epilogue(dir: str):
    """
    Remove everything we created for fast_diff_py
    """
    fdo = FastDifPy(default_cfg_path=os.path.join(dir, FastDifPy.default_config_file))
    fdo.config.retain_progress = False
    fdo.config.delete_thumb = True
    fdo.config.delete_db = True
    fdo.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Benchmark for Duplicate Image Finder Implementations FastDiffPy and DifPy;'
                    ' run deduplication benchmark')

    parser.add_argument("-p", "--processes",
                        help="List of process numbers to use",
                        nargs="+", type=int, default=number_of_processes, required=False)
    parser.add_argument("-s", "--size",
                        help="Size of image to use", type=int, default=target_size)
    parser.add_argument("-a", "--attempts",
                        help="Number of times to attempt to do for each number of process",
                        type=int, default=retries)
    parser.add_argument("-u", "--partition_a", help="Directory to be used for partition_a",
                        type=str, required=True)
    parser.add_argument("-t", "--target",
                        help="Target File, where the statistics of the benchmark are stored, "
                             "defaults to {PWD}/benchmark_compression_stats_YYYY-MM-DD_HH-MM-SS.json",
                        required=False)

    # optional
    parser.add_argument("-v", "--partition_b",type=str,
                        help="Directory to be used for partition_b, can be left empty")
    parser.add_argument("-g", "--temp", type=str,
                        help="Provide a directory, where some checkpoints are going to be saved")
    parser.add_argument("-r", "--rotate",
                        help="Disable rotation of the image",
                        action="store_false")
    parser.add_argument("-l", "--lazy",
                        help="Enable lazy mode",
                        action="store_true")
    parser.add_argument("-d", "--delta", type=float,
                        help="Delta between images to be achieved for them to be considered duplicated", default=200.0)

    args = parser.parse_args()

    python = shutil.which("python3")
    source_dir = os.path.abspath(os.path.join(__file__, "..", "..", "src"))
    external_benchmark = os.path.abspath(os.path.join(os.path.dirname(__file__), "benchmark_external.py"))

    if args.temp is not None:
        os.makedirs(args.temp, exist_ok=True)
        td = args.temp

        file = os.path.join(args.temp, "diff_checkpoint.pickle")
    else:
        file = os.path.join(os.getcwd(), "diff_checkpoint.pickle")
        td = os.getcwd()

    if not 10 < args.size < 5000:
        raise ValueError("Size must be between 1 and 5000")

    if args.attempts < 1:
        raise ValueError("Attempts must be greater than 0")

    if args.delta < 0:
        raise ValueError("Delta must be greater than 0")

    # Setting stats file
    dts = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    if args.target is None:
        target = os.path.join(os.getcwd(),
                              f"benchmark_deduplication_stats_{dts}.json")
    else:
        target = args.target

    if args.partition_b is not None:
        partitions = [args.partition_b, args.partition_a]
    else:
        partitions = [args.partition_a]

    # Prepare the difpy object
    difpy_preamble(directory=partitions, size=args.size, file=file)

    # Prepare the fast_diff_py object
    fast_diff_preamble(directory=partitions, rotate=args.rotate, size=args.size, task_dir=td)

    subjects = ["difpy", "fast_diff_py"]
    # subjects = ["fast_diff_py"]
    stats = {}

    for subject in subjects:
        stats[subject] = {}
        for p in args.processes:
            stats[subject][p] = []
            for a in range(args.attempts):
                print(f"Performing Benchmark with {subject}, attempt {a + 1} of {args.attempts}, processes {p}")

                command = [python, external_benchmark, "-p", f"{p}", "-f", file, "-d", td, "-s", f"{args.delta}"]

                # Add flags for lazy and rotate
                if args.lazy:
                    command.append("-l")

                if not args.rotate:
                    command.append("-r")

                # Set the mode
                command.append(subject.upper())

                start = datetime.datetime.now(datetime.UTC)
                proc = subprocess.Popen(
                    command,
                    env={"PYTHONPATH": source_dir},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, universal_newlines=True)

                # Read the output
                for line in iter(proc.stdout.readline, ""):
                    print(f"{line.strip()}")

                # Read the stderr
                for line in iter(proc.stderr.readline, ""):
                    print(f"{line.strip()}")

                return_code = proc.poll()
                if return_code is not None:
                    print(f'RETURN CODE', return_code)

                end = datetime.datetime.now(datetime.UTC)
                time = (end - start).total_seconds()

                # Storing the time taken
                stats[subject][p].append(time)


                # Writing progress to file
                with open(target, "w") as f:
                    json.dump(stats, f)

    # Cleaning up afterwards
    difpy_epilogue(file)
    fast_diff_epilogue(td)

print("Done Deduplications Benchmark")
