# Sample 07 - SHELL INJECTION: untrusted input interpolated into a shell string.
# Expected decision: deny  (EXEC_SHELL_INJECTION, HIGH)
import os
import sys

user_input = sys.argv[1] if len(sys.argv) > 1 else "."

# f-string interpolation into os.system is a classic injection vector.
os.system(f"ls {user_input}")
