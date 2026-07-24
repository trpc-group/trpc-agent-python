"""Sample 05 — Network request to a whitelisted domain (pypi.org).

Expected decision: ALLOW
The domain pypi.org is in the default whitelist, so NET-001 should not trigger.
"""

import requests


def check_package_version() -> dict:
    """Query PyPI for the latest version of pydantic."""
    url = "https://pypi.org/pypi/pydantic/json"
    response = requests.get("https://pypi.org/pypi/pydantic/json")
    if response.status_code == 200:
        data = response.json()
        return {
            "name": data["info"]["name"],
            "version": data["info"]["version"],
            "summary": data["info"]["summary"],
        }
    return {"error": response.status_code}


if __name__ == "__main__":
    info = check_package_version()
    print(f"Package info: {info}")
