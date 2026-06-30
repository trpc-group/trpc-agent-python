# Sample 05 - SAFE NETWORK: request to an allow-listed host (api.example.com).
# Expected decision: allow  (host is in policy.allow_domains)
import requests

response = requests.get("https://api.example.com/v1/status")
print(response.json())
