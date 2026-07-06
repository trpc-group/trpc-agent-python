# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Input resolution: --diff-file / --repo-path / --files / --fixture → RawChangeSet."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from dataclasses import field
from typing import Dict
from typing import List
from typing import Optional

from .config import FIXTURES_DIR

INPUT_DIFF_FILE = "diff_file"
INPUT_REPO_PATH = "repo_path"
INPUT_FILE_LIST = "file_list"
INPUT_FIXTURE = "fixture"

_MAX_FILE_BYTES = 512_000  # skip pathological blobs when snapshotting file contents


@dataclass
class RawChangeSet:
    """Normalized review input: a unified diff plus optional full file contents."""

    input_type: str
    input_ref: str
    unified_diff_text: str
    file_contents: Dict[str, str] = field(default_factory=dict)


def _run_git(repo_path: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return result.stdout


def _read_text(path: str) -> Optional[str]:
    try:
        if os.path.getsize(path) > _MAX_FILE_BYTES:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _synthesize_add_diff(rel_path: str, content: str) -> str:
    lines = content.splitlines()
    body = "".join(f"+{line}\n" for line in lines)
    count = len(lines)
    return (f"diff --git a/{rel_path} b/{rel_path}\n"
            f"new file mode 100644\n"
            f"--- /dev/null\n"
            f"+++ b/{rel_path}\n"
            f"@@ -0,0 +1,{count} @@\n"
            f"{body}")


def from_diff_file(path: str) -> RawChangeSet:
    """Read a unified diff / PR patch file."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return RawChangeSet(input_type=INPUT_DIFF_FILE, input_ref=os.path.abspath(path), unified_diff_text=text)


def from_fixture(name: str) -> RawChangeSet:
    """Load one of the committed test fixtures by bare name (e.g. ``security_issue``)."""
    filename = name if name.endswith(".diff") else f"{name}.diff"
    path = os.path.join(FIXTURES_DIR, filename)
    if not os.path.isfile(path):
        available = sorted(f[:-5] for f in os.listdir(FIXTURES_DIR) if f.endswith(".diff"))
        raise FileNotFoundError(f"unknown fixture {name!r}; available: {', '.join(available)}")
    changeset = from_diff_file(path)
    changeset.input_type = INPUT_FIXTURE
    changeset.input_ref = filename
    return changeset


def from_repo_path(repo_path: str) -> RawChangeSet:
    """Collect working-tree changes of a git repo (tracked diff + untracked adds)."""
    repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        raise ValueError(f"{repo_path} is not a git repository (missing .git)")

    diff_text = _run_git(repo_path, "diff", "HEAD")
    untracked = _run_git(repo_path, "ls-files", "--others", "--exclude-standard").splitlines()

    file_contents: Dict[str, str] = {}
    extra_diffs: List[str] = []
    for rel in untracked:
        content = _read_text(os.path.join(repo_path, rel))
        if content is None:
            continue
        extra_diffs.append(_synthesize_add_diff(rel, content))
        file_contents[rel] = content

    changed = _run_git(repo_path, "diff", "HEAD", "--name-only").splitlines()
    for rel in changed:
        content = _read_text(os.path.join(repo_path, rel))
        if content is not None:
            file_contents[rel] = content

    return RawChangeSet(
        input_type=INPUT_REPO_PATH,
        input_ref=repo_path,
        unified_diff_text=diff_text + "".join(extra_diffs),
        file_contents=file_contents,
    )


def from_file_list(paths: List[str], base_dir: str = "") -> RawChangeSet:
    """Treat a list of files as wholly-added changes (no VCS required)."""
    base = os.path.abspath(base_dir or os.getcwd())
    file_contents: Dict[str, str] = {}
    diffs: List[str] = []
    for path in paths:
        abs_path = path if os.path.isabs(path) else os.path.join(base, path)
        content = _read_text(abs_path)
        if content is None:
            raise FileNotFoundError(f"cannot read {abs_path}")
        rel = os.path.relpath(abs_path, base).replace(os.sep, "/")
        diffs.append(_synthesize_add_diff(rel, content))
        file_contents[rel] = content
    return RawChangeSet(
        input_type=INPUT_FILE_LIST,
        input_ref=",".join(paths),
        unified_diff_text="".join(diffs),
        file_contents=file_contents,
    )
