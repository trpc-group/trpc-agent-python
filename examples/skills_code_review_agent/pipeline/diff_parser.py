# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Parse review inputs into a ``DiffSummary`` (issue #92, requirement 3).

Plumbing only — wraps the mature ``unidiff`` parser. Three input kinds:
  * a unified-diff text / file (``--diff-file``)
  * a git worktree (``--repo-path`` → ``git diff``)
  * a fixture diff (same as diff-file)
The one thing reviewers actually consume downstream is ``Hunk.candidate_lines`` — the new-file
line numbers of added/changed lines — so scanners only report on what the diff touched.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from unidiff import PatchSet

from .types import ChangedFile, DiffSummary, Hunk

_LANG_BY_SUFFIX = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".sh": "bash",
}


def _language(path: str) -> str | None:
    return _LANG_BY_SUFFIX.get(Path(path).suffix.lower())


def _change_type(pf) -> str:
    if pf.is_added_file:
        return "added"
    if pf.is_removed_file:
        return "deleted"
    if pf.is_rename:
        return "renamed"
    return "modified"


def parse_unified_diff(text: str) -> DiffSummary:
    patch = PatchSet(text)
    files: list[ChangedFile] = []
    added = removed = 0
    languages: dict[str, int] = {}

    for pf in patch:
        path = pf.path
        lang = _language(path)
        if lang:
            languages[lang] = languages.get(lang, 0) + 1
        hunks: list[Hunk] = []
        for h in pf:
            candidate = [ln.target_line_no for ln in h if ln.is_added and ln.target_line_no is not None]
            hunks.append(
                Hunk(
                    old_start=h.source_start,
                    old_len=h.source_length,
                    new_start=h.target_start,
                    new_len=h.target_length,
                    candidate_lines=candidate,
                ))
        added += pf.added
        removed += pf.removed
        files.append(ChangedFile(path=path, change_type=_change_type(pf), language=lang, hunks=hunks))

    return DiffSummary(files=files, files_changed=len(files), added=added, removed=removed, languages=languages)


def parse_diff_file(path: str) -> DiffSummary:
    return parse_unified_diff(Path(path).read_text(encoding="utf-8"))


def parse_git_worktree(repo_path: str, base_ref: str | None = None) -> DiffSummary:
    args = ["git", "-C", repo_path, "diff", "--unified=3"]
    if base_ref:
        args.append(base_ref)
    proc = subprocess.run(args, capture_output=True, text=True, check=True)
    return parse_unified_diff(proc.stdout)


def materialize_new_files(text: str) -> dict[str, str]:
    """Reconstruct the post-change (target-side) content of each changed file from a diff.

    For an added file this is the complete file, so scanners can run on real source. For a
    modified file (diff-only mode, no base) it is the target-side lines present in the hunks —
    best-effort; use ``--repo-path`` when the full working tree is available.
    """
    patch = PatchSet(text)
    out: dict[str, str] = {}
    for pf in patch:
        if pf.is_removed_file:
            continue
        lines: list[str] = []
        for h in pf:
            for ln in h:
                if ln.is_added or ln.is_context:
                    lines.append(ln.value.rstrip("\n"))
        out[pf.path] = "\n".join(lines) + "\n"
    return out
