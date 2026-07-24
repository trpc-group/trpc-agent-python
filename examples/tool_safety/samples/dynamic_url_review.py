import requests

base_url = input("url: ")
requests.get(base_url + "/collect", timeout=5)
