"""A non-allowlisted network request sample."""

import requests

response = requests.get("https://collector.example.net/upload", timeout=10)
print(response.status_code)
