# Sample 02 — Dangerous deletion
# Expected decision: deny
# Description: Recursively deletes a system directory — must be blocked.

import shutil

shutil.rmtree("/")
