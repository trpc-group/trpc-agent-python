#!/usr/bin/env python3
"""Sample 05 — NETWORK to an ALLOW-LISTED host: request to api.openai.com.

Expected verdict: allow (domain-aware refinement drops the network hit because
api.openai.com is on the policy allow-list).
"""

import requests

response = requests.get("https://api.openai.com/v1/models", timeout=10)
print(response.status_code)
