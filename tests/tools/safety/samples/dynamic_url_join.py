import requests

host = input("host: ")
requests.get("https://" + host + "/status")
