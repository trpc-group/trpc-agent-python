"""Tests for standalone tool_safety_check subprocess behavior."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_tool_safety_check_subprocess_exit_codes(tmp_path) -> None:
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "tool_safety_check.py"
    safe_script = tmp_path / "safe.py"
    deny_script = tmp_path / "deny.sh"
    review_script = tmp_path / "review.sh"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    safe_script.write_text("print('ok')\n", encoding="utf-8")
    deny_script.write_text("rm -rf /tmp/demo\n", encoding="utf-8")
    review_script.write_text("pip install demo\n", encoding="utf-8")

    safe = subprocess.run([sys.executable, str(script_path), str(safe_script)],
                          check=False,
                          capture_output=True,
                          text=True,
                          env=env)
    deny = subprocess.run([sys.executable, str(script_path), str(deny_script)],
                          check=False,
                          capture_output=True,
                          text=True,
                          env=env)
    review = subprocess.run([sys.executable, str(script_path), str(review_script)],
                            check=False,
                            capture_output=True,
                            text=True,
                            env=env)

    assert safe.returncode == 0
    assert json.loads(safe.stdout)["decision"] == "allow"
    assert deny.returncode == 1
    assert json.loads(deny.stdout)["decision"] == "deny"
    assert review.returncode == 2
    assert json.loads(review.stdout)["decision"] == "needs_human_review"
