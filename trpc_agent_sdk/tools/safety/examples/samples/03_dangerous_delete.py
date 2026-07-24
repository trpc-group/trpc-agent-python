"""Recursive delete sample.

Expected scan result: decision=deny, rule_ids contains FILE001_RECURSIVE_DELETE.
"""

from __future__ import annotations

import shutil


def purge_workspace(path: str) -> None:
    shutil.rmtree(path)


if __name__ == "__main__":
    purge_workspace("/tmp/scratch")
