# Sample 08 — Dependency install
# Expected decision: deny
# Description: Installs a package at runtime, which changes the execution
#   environment — must be blocked.

import subprocess

subprocess.run(["pip", "install", "evil-package"])
