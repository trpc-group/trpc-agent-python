import subprocess

user_cmd = input("command: ")
subprocess.run(user_cmd, shell=True, check=False)
