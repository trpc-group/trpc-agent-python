#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unified-diff parser → ChangeSet (Phase 1, L1).

Parses a standard unified diff (as produced by ``git diff`` / ``diff -u``)
into a structured :class:`ChangeSet` of files → hunks → lines, preserving
new-file line numbers so downstream rule engines can pin findings to exact
locations.

Line-number rule
----------------
``new_line_no`` starts at ``c`` from the hunk header ``@@ -a,b +c,d @@``
and increments on every ``add`` / ``ctx`` line. ``del`` lines carry
``new_line_no = None`` (they don't exist in the new file).

Tolerates (skips without error): binary files, renames, mode changes,
``\\ No newline at end of file`` markers. An empty diff yields an empty
``ChangeSet``.

Usage
-----
    from parse_diff import parse_diff, ChangeSet
    cs = parse_diff(diff_text)

    # CLI (stdin or file path):
    python parse_diff.py < my.diff
    python parse_diff.py my.diff          # prints JSON to stdout
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import Iterator

# Hunk header: @@ -a,b +c,d @@  (counts are optional, e.g. @@ -1 +1 @@)
_HUNK_RE = re.compile(r"^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@")

# diff --git a/foo b/foo
_GIT_DIFF_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
# --- a/foo  or  --- /dev/null
_OLD_FILE_RE = re.compile(r"^--- (?:a/)?(.+)$")
# +++ b/foo  or  +++ /dev/null
_NEW_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")


@dataclass
class DiffLine:
    """One line inside a hunk.

    ``new_line_no`` is the 1-based line number in the *new* file. It is set
    for ``add`` / ``ctx`` lines and ``None`` for ``del`` lines (which only
    exist in the old file).
    """

    type: str  # "add" | "del" | "ctx"
    content: str
    new_line_no: int | None = None


@dataclass
class Hunk:
    """One ``@@ -a,b +c,d @@`` block."""

    old_start: int
    new_start: int
    old_count: int
    new_count: int
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class ChangedFile:
    """One file touched by the diff."""

    path: str
    status: str  # "added" | "modified" | "deleted"
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def added_lines(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.type == "add")

    @property
    def deleted_lines(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.type == "del")

    @property
    def line_count(self) -> int:
        """Total diff lines across all hunks (add + del + ctx)."""
        return sum(len(h.lines) for h in self.hunks)

    @property
    def hunk_count(self) -> int:
        return len(self.hunks)


@dataclass
class ChangeSet:
    """Structured representation of an entire diff."""

    files: list[ChangedFile] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.files)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def parse_diff(diff_text: str) -> ChangeSet:
    """Parse a unified diff string into a :class:`ChangeSet`.

    Robust to git diff variants: binary files, renames, mode changes are
    skipped without raising. An empty / whitespace-only input returns an
    empty ``ChangeSet``.
    """
    cs = ChangeSet()
    if not diff_text or not diff_text.strip():
        return cs

    # Line iterator — one pass, no full copies. Keep trailing newline stripped
    # so the diff-line sigils (+/-/space) are at column 0.
    lines = diff_text.splitlines()
    cur_file: ChangedFile | None = None
    cur_hunk: Hunk | None = None
    new_line_no = 0
    # Track whether the current file has real content hunks vs. only meta
    # lines (binary/rename/mode) — meta-only files are dropped at the end.
    file_has_hunks = False

    def _flush_hunk() -> None:
        nonlocal cur_hunk
        if cur_hunk is not None and cur_file is not None:
            cur_file.hunks.append(cur_hunk)
            cur_hunk = None

    def _flush_file() -> None:
        nonlocal cur_file
        _flush_hunk()
        if cur_file is not None and (file_has_hunks or cur_file.hunks):
            cs.files.append(cur_file)
        cur_file = None

    for raw in lines:
        # --- file boundary: diff --git a/x b/x ---
        m = _GIT_DIFF_RE.match(raw)
        if m:
            _flush_file()
            # Use the b/ path as the canonical new path; status decided later
            # when we see --- / +++ lines.
            cur_file = ChangedFile(path=m.group(2), status="modified")
            file_has_hunks = False
            continue

        # --- old file line: --- a/x  /  --- /dev/null ---
        if cur_file is not None and raw.startswith("--- "):
            m = _OLD_FILE_RE.match(raw)
            if m and m.group(1) == "/dev/null":
                cur_file.status = "added"
            continue

        # --- new file line: +++ b/x  /  +++ /dev/null ---
        if cur_file is not None and raw.startswith("+++ "):
            m = _NEW_FILE_RE.match(raw)
            if m:
                if m.group(1) == "/dev/null":
                    cur_file.status = "deleted"
                else:
                    # Prefer the explicit +++ b/path over the git header path.
                    cur_file.path = m.group(1)
            continue

        # --- hunk header: @@ -a,b +c,d @@ ---
        m = _HUNK_RE.match(raw)
        if m and cur_file is not None:
            _flush_hunk()
            old_start = int(m.group(1))
            new_start = int(m.group(3))
            old_count = int(m.group(2)) if m.group(2) else 1
            new_count = int(m.group(4)) if m.group(4) else 1
            cur_hunk = Hunk(
                old_start=old_start,
                new_start=new_start,
                old_count=old_count,
                new_count=new_count,
            )
            new_line_no = new_start
            file_has_hunks = True
            continue

        # --- inside a hunk ---
        if cur_hunk is not None:
            # add line
            if raw.startswith("+"):
                cur_hunk.lines.append(
                    DiffLine("add", raw[1:], new_line_no)
                )
                new_line_no += 1
            elif raw.startswith("-"):
                cur_hunk.lines.append(DiffLine("del", raw[1:], None))
            elif raw.startswith(" "):
                cur_hunk.lines.append(
                    DiffLine("ctx", raw[1:], new_line_no)
                )
                new_line_no += 1
            elif raw.startswith("\\"):
                # "\ No newline at end of file" — meta marker, skip.
                continue
            else:
                # Unexpected line inside a hunk (e.g. truncated); stop hunk.
                _flush_hunk()
            continue

        # --- meta lines outside any hunk (tolerate & skip) ---
        # Binary files a/x b/x differ
        # rename from / rename to / old mode / new mode / new file mode /
        # deleted file mode / index abc..def 100644 / similarity index ...
        if cur_file is not None and _is_meta_line(raw):
            continue

        # A "+++ b/foo" with no preceding "diff --git" (simplified diff) —
        # start a file lazily so we still parse header-less diffs.
        if cur_file is None and raw.startswith("+++ "):
            cur_file = ChangedFile(path="unknown", status="modified")
            file_has_hunks = False
            m = _NEW_FILE_RE.match(raw)
            if m and m.group(1) != "/dev/null":
                cur_file.path = m.group(1)
            continue

    _flush_file()
    return cs


def _is_meta_line(raw: str) -> bool:
    """True for git meta lines that carry no source hunks (skip gracefully)."""
    return (
        raw.startswith("Binary files")
        or raw.startswith("rename from")
        or raw.startswith("rename to")
        or raw.startswith("old mode")
        or raw.startswith("new mode")
        or raw.startswith("new file mode")
        or raw.startswith("deleted file mode")
        or raw.startswith("similarity index")
        or raw.startswith("dissimilarity index")
        or raw.startswith("copy from")
        or raw.startswith("copy to")
        or raw.startswith("index ")
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _read_input(argv: list[str]) -> str:
    if len(argv) > 1 and argv[1] not in ("-",):
        with open(argv[1], "r", encoding="utf-8") as fh:
            return fh.read()
    return sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    cs = parse_diff(_read_input(argv))
    print(cs.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
