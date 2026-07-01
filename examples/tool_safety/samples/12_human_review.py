# Sample 12: Human review scenario.
# Uses dynamic network target that cannot be statically resolved.
# Scanner returns NEEDS_HUMAN_REVIEW (MEDIUM) because safety is uncertain.
import os

# Dynamic URL from env: cannot prove allow-listed or not.
target_url = os.environ.get("TARGET_URL", "https://default.example.com")
import requests
requests.get(target_url)

# Dynamic file path
target_file = os.environ.get("FILE_PATH", "/tmp/safe")
with open(target_file) as f:
    data = f.read()
