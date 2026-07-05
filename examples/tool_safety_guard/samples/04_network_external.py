import requests

response = requests.get("https://evil.example.net/collect", timeout=5)
print(response.status_code)
