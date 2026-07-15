"""Sample safe Python script.

Expected scan result: decision=allow, rule_ids=['SAFE000'].
"""

from __future__ import annotations

import os


def list_cwd() -> list[str]:
    return sorted(os.listdir("."))


def main() -> None:
    for entry in list_cwd():
        print(entry)


if __name__ == "__main__":
    main()
