"""Shared Git repository fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def sample_repository(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "config", "user.email", "review@example.com")
    git(repository, "config", "user.name", "Review Test")
    source = repository / "app.py"
    source.write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    git(repository, "add", "app.py")
    git(repository, "commit", "-qm", "base")
    base = git(repository, "rev-parse", "HEAD")

    source.write_text(
        "def divide(a, b):\n"
        "    if b == 0:\n"
        "        return None\n"
        "    return a / b\n",
        encoding="utf-8",
    )
    (repository / "README.md").write_text("# Demo\n", encoding="utf-8")
    git(repository, "add", "app.py", "README.md")
    git(repository, "commit", "-qm", "head")
    head = git(repository, "rev-parse", "HEAD")
    return repository, base, head
