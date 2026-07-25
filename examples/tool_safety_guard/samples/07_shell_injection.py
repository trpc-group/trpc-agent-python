"""A shell injection sample."""

import os

branch = input("Branch: ")
os.system(f"git checkout {branch}")
