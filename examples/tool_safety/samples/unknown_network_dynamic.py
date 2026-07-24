import requests

url = input("url: ")
requests.get(url, timeout=3)
