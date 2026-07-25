"""A process invocation that requires human review."""

import subprocess

subprocess.run(["git", "status"], check=True)
