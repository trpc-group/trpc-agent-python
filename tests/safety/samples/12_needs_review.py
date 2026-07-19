# Sample 12 — Needs human review
# Expected decision: needs_human_review
# Description: Uses subprocess.run with a list of arguments (no shell=True),
#   which is relatively safe but still spawns a subprocess.  This falls into
#   the "needs_human_review" tier — not dangerous enough to deny, but not
#   safe enough to automatically allow.

import subprocess

result = subprocess.run(["ls", "-la"], capture_output=True, text=True)
print(result.stdout)
