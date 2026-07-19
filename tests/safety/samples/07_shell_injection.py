# Sample 07 — Shell injection
# Expected decision: deny
# Description: Uses subprocess.run with shell=True and a string command,
#   which is vulnerable to shell injection — must be blocked.

import subprocess

subprocess.run("rm -rf /", shell=True)
