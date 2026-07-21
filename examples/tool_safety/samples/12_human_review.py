# Static scanner sample; do not execute.

import requests

target_url = input("target URL: ")
requests.get(target_url, timeout=5)
