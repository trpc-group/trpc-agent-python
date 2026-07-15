"""Unbounded loop sample.

Expected scan result: decision=deny, rule_ids contains RES001_UNBOUNDED_LOOP.
"""

from __future__ import annotations


def spam() -> None:
    while True:
        print("looping forever")


if __name__ == "__main__":
    spam()
