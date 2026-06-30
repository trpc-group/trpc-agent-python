# Sample 02 - DANGEROUS DELETE: recursive removal of the filesystem root.
# Expected decision: deny  (FILE_RM_RF, CRITICAL)
import os
import shutil

# Recursive delete of the root directory.
shutil.rmtree("/")

# Same intent via the shell.
os.system("rm -rf /")
