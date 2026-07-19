# Sample 06 — Subprocess call (safe form, list args)
# Expected decision: needs_human_review
# Description: Uses subprocess.run with a list of arguments (no shell=True).
#   This is safer than shell=True but still spawns a subprocess, so it
#   requires human review rather than being automatically allowed.

import subprocess

subprocess.run(["ls", "-la"])
