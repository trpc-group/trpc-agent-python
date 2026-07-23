import requests

response = requests.get("https://api.github.com/repos/trpc-group/trpc-agent-python", timeout=5)
print(response.status_code)
