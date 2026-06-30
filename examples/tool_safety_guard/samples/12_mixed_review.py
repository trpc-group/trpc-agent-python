# Sample 12 - MIXED MEDIUM: several review-level signals, none denied on its own.
# Expected decision: needs_human_review
import os
import subprocess

# Spawning a process -> review.
subprocess.run(["git", "status"], check=False)

# Loosening permissions -> review.
os.system("chmod 777 /tmp/workdir")

# Installing a dependency -> review.
os.system("pip install black")
