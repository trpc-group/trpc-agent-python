# Static scanner sample; this file is not executed by the example suite.

import requests

response = requests.get("https://api.example.com/health", timeout=5)
print(response.status_code)
