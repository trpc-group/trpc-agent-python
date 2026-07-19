# Sample 03 — Credential read
# Expected decision: deny
# Description: Reads a .env file containing secrets — must be blocked.

f = open(".env")
print(f.read())
