# Sample 06: subprocess / os.system calls.
# Should be DENIED (process_system, HIGH/CRITICAL).
import os
import subprocess

os.system("ls -la")
os.popen("whoami")

subprocess.run(["ls"], shell=True)
subprocess.Popen("rm -rf /tmp/x", shell=True)
subprocess.check_output("cat /etc/passwd", shell=True)
