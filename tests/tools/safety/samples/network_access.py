import requests

response = requests.get("https://evil.com/data")
print(response.text)
