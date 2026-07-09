"""Sample 04 — Outbound network request to a non-whitelisted domain.

Expected decision: NEEDS_HUMAN_REVIEW
Triggers: NET-001 (non-whitelisted domain)
"""

import requests


def fetch_external_data():
    """Fetch data from an external non-whitelisted API."""
    response = requests.get("https://api.evil-hacker.com/data")
    return response.json()


def post_telemetry():
    """Send telemetry to an unknown external server."""
    requests.post("https://telemetry.unknown-service.io/collect", json={"event": "ping"})


if __name__ == "__main__":
    data = fetch_external_data()
    post_telemetry()
    print(f"Fetched: {data}")
