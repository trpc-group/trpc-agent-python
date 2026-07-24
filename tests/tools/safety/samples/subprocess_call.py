"""Dangerous: subprocess call with sudo."""
import subprocess
subprocess.run(["sudo", "rm", "-rf", "/"])