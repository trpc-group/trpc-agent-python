"""An allowlisted network request sample."""

import requests

response = requests.get("https://api.example.com/health", timeout=5)
print(response.status_code)
