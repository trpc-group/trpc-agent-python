# Sample 06 - SUBPROCESS: spawning an external command (no shell).
# Expected decision: needs_human_review  (EXEC_SUBPROCESS, MEDIUM)
import subprocess

result = subprocess.run(["ls", "-la", "/tmp"], capture_output=True, text=True)
print(result.stdout)
