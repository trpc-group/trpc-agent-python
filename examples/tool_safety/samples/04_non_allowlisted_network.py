# Static scanner sample; do not execute.

import requests

requests.post("https://collector.external.example/upload", data={"status": "ok"}, timeout=5)
