"""Secret-to-output sample.

Expected scan result: decision=deny, rule_ids contains SECRET001_LOG_SINK.
"""

from __future__ import annotations

import os


def leak() -> None:
    token = os.environ["API_TOKEN"]
    print(f"token is {token}")


if __name__ == "__main__":
    leak()
