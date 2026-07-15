"""Allowlist network sample.

Expected scan result: decision=allow, rule_ids=['SAFE000'].
"""

from __future__ import annotations

import requests


def fetch_user(handle: str) -> dict:
    response = requests.get(
        f"https://api.github.com/users/{handle}", timeout=5,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    print(fetch_user("octocat"))
