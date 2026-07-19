# Sample 04 — Network egress to non-whitelisted domain
# Expected decision: deny
# Description: Makes an HTTP request to a domain not in the whitelist.

import requests

requests.get("http://evil.com/data")
