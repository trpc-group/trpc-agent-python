"""Shell injection sample.

Expected scan result: decision=deny, rule_ids contains PROC002_SHELL_INJECTION.
"""

from __future__ import annotations

import subprocess


def run_untrusted(user_input: str) -> int:
    return subprocess.run(
        f"ls {user_input}",
        shell=True,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(run_untrusted("; rm -rf /"))
