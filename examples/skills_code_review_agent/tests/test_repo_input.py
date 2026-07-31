# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business tests for Git worktree input normalization."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from examples.skills_code_review_agent.agent.input_parser import InputParseError
from examples.skills_code_review_agent.agent.input_parser import parse_repo_path


def test_tracked_worktree_change_is_parsed(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "initial")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")

    summary = parse_repo_path(repo, task_id="repo-1")

    changed = summary.changed_files[0]
    assert changed.path == "app.py"
    assert changed.added_lines == 1
    assert changed.deleted_lines == 1


def test_staged_and_unstaged_changes_are_read_together(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "initial")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    (repo / "other.py").write_text("flag = False\n", encoding="utf-8")
    _git(repo, "add", "other.py")
    (repo / "other.py").write_text("flag = True\n", encoding="utf-8")

    summary = parse_repo_path(repo, task_id="repo-2")

    assert {item.path for item in summary.changed_files} == {"app.py", "other.py"}


def test_untracked_text_file_is_parsed_as_added_file(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "new.py").write_text("print('new')\n", encoding="utf-8")

    summary = parse_repo_path(repo, task_id="repo-3")

    changed = summary.changed_files[0]
    assert changed.path == "new.py"
    assert changed.status == "added"
    assert changed.candidate_lines == [1]


def test_untracked_binary_file_is_marked_binary(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "asset.bin").write_bytes(b"\x00\x01\x02")

    summary = parse_repo_path(repo, task_id="repo-4")

    changed = summary.changed_files[0]
    assert changed.path == "asset.bin"
    assert changed.status == "added"
    assert changed.is_binary is True


def test_deleted_file_is_identified_from_repo_diff(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "gone.txt").write_text("gone\n", encoding="utf-8")
    _git(repo, "add", "gone.txt")
    _git(repo, "commit", "-qm", "initial")
    (repo / "gone.txt").unlink()

    summary = parse_repo_path(repo, task_id="repo-5")

    assert summary.changed_files[0].status == "deleted"


def test_non_git_directory_returns_clear_error(tmp_path: Path):
    with pytest.raises(InputParseError, match="git command failed"):
        parse_repo_path(tmp_path, task_id="repo-6")


def test_git_timeout_returns_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=1)

    monkeypatch.setattr("examples.skills_code_review_agent.agent.input_parser.subprocess.run", raise_timeout)
    with pytest.raises(InputParseError, match="timed out"):
        parse_repo_path(repo, task_id="repo-7")


def test_repo_path_with_spaces_is_supported(tmp_path: Path):
    repo = _init_repo(tmp_path / "repo with spaces")
    (repo / "new file.py").write_text("x = 1\n", encoding="utf-8")

    summary = parse_repo_path(repo, task_id="repo-8")

    assert summary.changed_files[0].path == "new file.py"


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "review@example.com")
    _git(path, "config", "user.name", "Review Test")
    return path


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
