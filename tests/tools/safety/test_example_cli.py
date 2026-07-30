# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the public Tool Script Safety Guard CLI."""

from pathlib import Path
import sys

import pytest

from examples.tool_safety_guard.run_safety_check import _language
from examples.tool_safety_guard.run_safety_check import main
from trpc_agent_sdk.tools.safety import ScriptLanguage


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("check.py", ScriptLanguage.PYTHON),
        ("check.PY", ScriptLanguage.PYTHON),
        ("check.sh", ScriptLanguage.BASH),
        ("check.bash", ScriptLanguage.BASH),
    ],
)
def test_language_inference_uses_known_suffixes(filename, expected):
    assert _language(Path(filename)) == expected


def test_language_inference_accepts_explicit_language_for_unknown_suffix():
    assert _language(Path("check.txt"), "python") == ScriptLanguage.PYTHON


def test_language_inference_rejects_unknown_suffix():
    with pytest.raises(ValueError, match="use --language"):
        _language(Path("check.txt"))


def test_cli_reports_unknown_suffix_as_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run_safety_check.py", "check.txt"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2
    assert "cannot infer language for check.txt" in capsys.readouterr().err
