"""Sample 12 — Human review scenario: raw socket + non-whitelisted domain.

Expected decision: NEEDS_HUMAN_REVIEW
Triggers: NET-002 (raw socket usage) + NET-001 (non-whitelisted domain)
"""

import socket

import requests


def check_port(host: str, port: int) -> bool:
    """Check if a port is open using raw socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        result = sock.connect_ex((host, port))
        return result == 0
    finally:
        sock.close()


def query_unknown_api():
    """Make request to an unknown domain."""
    response = requests.get("https://internal-monitoring.corp.local/health")
    return response.status_code


if __name__ == "__main__":
    is_open = check_port("unknown-server.local", 8080)
    print(f"Port 8080 open: {is_open}")
    status = query_unknown_api()
    print(f"API status: {status}")
