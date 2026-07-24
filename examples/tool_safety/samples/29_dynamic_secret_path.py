# Sample 29: dynamic credential path construction.
import os
path = os.path.join(os.path.expanduser("~"), ".ssh", "id_rsa")
with open(path) as f:
    print(f.read())
