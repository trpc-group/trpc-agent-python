# Static scanner sample; do not execute.

import subprocess

user_value = input("value: ")
subprocess.run("echo " + user_value, shell=True, check=True)
