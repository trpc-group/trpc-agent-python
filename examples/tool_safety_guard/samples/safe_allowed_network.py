import requests

requests.get(
    "https://api.example.com/data",
    allow_redirects=False,
    timeout=5,
)
