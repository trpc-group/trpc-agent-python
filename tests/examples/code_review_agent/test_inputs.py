# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for code review input loading."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from examples.code_review_agent.agent.inputs import load_review_input
from examples.code_review_agent.agent.inputs import read_repo_diff

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "examples" / "code_review_agent" / "fixtures"


def test_load_diff_file_builds_redacted_summary() -> None:
    bundle = load_review_input(diff_file=FIXTURES / "hardcoded_secret.diff")

    assert bundle.review_input.input_type == "diff_file"
    assert bundle.review_input.changed_files == ["src/config.py"]
    assert bundle.review_input.diff_sha256
    assert "1 file" in bundle.review_input.diff_summary


def test_load_review_input_rejects_both_modes(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_review_input(diff_file=FIXTURES / "clean.diff", repo_path=tmp_path)


def test_read_repo_diff_uses_git_diff(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="diff --git a/a.py b/a.py\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    diff = read_repo_diff(tmp_path, base_ref="origin/main")

    assert diff.startswith("diff --git")
    assert calls == [["git", "diff", "--no-ext-diff", "--no-color", "origin/main...HEAD"]]
