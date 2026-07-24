"""Read SSH key sample.

Expected scan result: decision=deny, rule_ids contains FILE003_CREDENTIAL_READ.
"""

from __future__ import annotations

import os
from pathlib import Path


def dump_key(home: str) -> str:
    path = Path(home) / ".ssh" / "id_rsa"
    return path.read_text()


if __name__ == "__main__":
    print(dump_key(os.path.expanduser("~")))
