"""Allowed subprocess sample.

Expected scan result: decision=allow, rule_ids=['SAFE000'].

The executable ``python`` is on the allow list and the call uses
``shell=False`` with a static argv list, so PROC001 does not fire.
"""

from __future__ import annotations

import subprocess


def run_self_check() -> int:
    completed = subprocess.run(
        ["python", "-c", "print('allowed-subprocess-ok')"],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(run_self_check())
