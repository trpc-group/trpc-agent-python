"""Dangerous: read SSH private key."""
with open("/root/.ssh/id_rsa") as f:
    key = f.read()
print(key)