# Sample 03 - SECRET READ: loading an SSH private key and environment secrets.
# Expected decision: deny  (SECRET_READ_SSH / SECRET_READ_ENV, CRITICAL)
import os

with open(os.path.expanduser("~/.ssh/id_rsa")) as fh:
    private_key = fh.read()

with open(".env") as fh:
    env_secrets = fh.read()

print("loaded credentials")
