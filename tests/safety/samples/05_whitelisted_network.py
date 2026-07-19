# Sample 05 — Whitelisted network request
# Expected decision: allow
# Description: Makes an HTTP request to localhost, which is in the whitelist.

import requests

response = requests.get("http://localhost:8080/health")
print(response.status_code)
