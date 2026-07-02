import subprocess

user_input = input("file: ")
subprocess.run("cat " + user_input, shell=True, check=False)
