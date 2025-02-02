from fast_diff_py import FastDifPy, FirstLoopConfig, SecondLoopConfig, Config

# Build the configuration.
flc = FirstLoopConfig(compute_hash=True)
slc = SecondLoopConfig(skip_matching_hash=True, match_aspect_by=0)
a = "/home/alisot2000/Desktop/test-dirs/dir_a/"
b = "/home/alisot2000/Desktop/test-dirs/dir_c/"
cfg = Config(part_a=[a], part_b=b, second_loop=slc, first_loop=flc)

# Run the program
fdo = FastDifPy(config=cfg, purge=True)
fdo.full_index()
fdo.first_loop()
fdo.second_loop()
fdo.commit()

print("="*120)
for c in fdo.get_diff_clusters(matching_hash=True):
    print(c)
print("="*120)
for c in fdo.get_diff_clusters(matching_hash=True, dir_a=False):
    print(c)

# Remove the intermediates but retain the db for later inspection.
fdo.config.delete_thumb = False
fdo.config.retain_progress = False
fdo.commit()
fdo.cleanup()