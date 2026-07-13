import os

path = os.path.expanduser("~/.ssh/id_rsa")
with open(path) as f:
    content = f.read()
