# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the unified diff parser."""
import json
import subprocess
import sys
from pathlib import Path

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = EXAMPLE_ROOT / "fixtures"
SCRIPTS = EXAMPLE_ROOT / "skills" / "code-review" / "scripts"


def _clean_text():
    return (FIXTURES / "clean.diff").read_text(encoding="utf-8")


def test_parse_files_and_paths():
    from diffparse import parse_unified_diff
    files = parse_unified_diff(_clean_text())
    assert [f["path"] for f in files] == ["app/util.py", "tests/test_util.py"]


def test_added_line_numbers():
    from diffparse import parse_unified_diff
    files = parse_unified_diff(_clean_text())
    added = files[0]["added_lines"]
    assert added[0]["line"] == 2
    assert "add two numbers" in added[0]["text"]
    assert added[1]["line"] == 3


def test_summarize():
    from diffparse import parse_unified_diff, summarize
    s = summarize(parse_unified_diff(_clean_text()))
    assert s["files_changed"] == 2
    assert s["additions"] == 3
    assert s["deletions"] == 0


def test_parse_diff_cli():
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "parse_diff.py"), str(FIXTURES / "clean.diff")],
        capture_output=True, text=True, check=True)
    payload = json.loads(out.stdout)
    assert payload["summary"]["files_changed"] == 2
    assert payload["files"][0]["path"] == "app/util.py"
