# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unified diff parser — the single source of truth for diff parsing.

Used in two places:

* inside the sandbox: ``python3 scripts/parse_diff.py <in.diff> [out.json]``
  emits the structured JSON described below, and ``run_checks.py`` falls back
  to it when the host did not pre-parse the diff;
* on the host: ``review_agent/diff_parser.py`` loads this file via importlib
  so both sides always agree on the parse result.

Only the Python standard library is used.  Unparsable sections never raise:
they are skipped and recorded in ``errors``.

Output schema (``parse_unified_diff``)::

    {
      "files": [
        {
          "path": "src/x.py",           # post-image path
          "old_path": "src/old.py",     # pre-image path when renamed, else None
          "change_type": "added|modified|deleted|renamed|binary",
          "is_binary": false,
          "hunks": [
            {
              "old_start": 1, "old_count": 3, "new_start": 1, "new_count": 4,
              "lines": [["+", null, 1, "import os"], [" ", 1, 2, "..."], ...]
            }
          ]
        }
      ],
      "errors": ["<description of skipped garbage>", ...]
    }

Hunk line tuples are ``[tag, old_no, new_no, text]`` with tag one of
``" "``, ``"+"``, ``"-"`` and the side-specific 1-based line number or None.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Optional

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DIFF_GIT_RE = re.compile(r'^diff --git (?:"?a/(.+?)"?) (?:"?b/(.+?)"?)$')
_BINARY_RE = re.compile(r"^Binary files .* differ$")


def _strip_prefix(path: str) -> Optional[str]:
    """Normalize a ---/+++ path: drop a/ b/ prefixes, detect /dev/null."""
    path = path.strip()
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    # strip a trailing git timestamp ("path\t2026-01-01 ...")
    path = path.split("\t")[0]
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _new_file_entry() -> dict:
    return {"path": None, "old_path": None, "change_type": "modified", "is_binary": False, "hunks": []}


def parse_unified_diff(text: str) -> dict:
    """Parse unified diff text into the structured form documented above."""
    files: list[dict] = []
    errors: list[str] = []
    cur: Optional[dict] = None
    # pending flags gathered from git extended headers before ---/+++ appear
    pending: dict = {}

    def flush():
        nonlocal cur
        if cur is not None and cur.get("path"):
            files.append(cur)
        cur = None

    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        m = _DIFF_GIT_RE.match(line)
        if m:
            flush()
            cur = _new_file_entry()
            pending = {}
            # provisional paths from the diff --git header; ---/+++ refine them
            cur["old_path"] = m.group(1)
            cur["path"] = m.group(2)
            i += 1
            continue

        if line.startswith("new file mode"):
            pending["added"] = True
            i += 1
            continue
        if line.startswith("deleted file mode"):
            pending["deleted"] = True
            i += 1
            continue
        if line.startswith("rename from "):
            if cur is not None:
                cur["old_path"] = line[len("rename from "):].strip()
                pending["renamed"] = True
            i += 1
            continue
        if line.startswith("rename to "):
            if cur is not None:
                cur["path"] = line[len("rename to "):].strip()
                pending["renamed"] = True
            i += 1
            continue
        if _BINARY_RE.match(line) or line.startswith("GIT binary patch"):
            if cur is None:
                cur = _new_file_entry()
            cur["is_binary"] = True
            cur["change_type"] = "binary"
            i += 1
            continue

        if line.startswith("--- "):
            old = _strip_prefix(line[4:])
            new = None
            if i + 1 < n and lines[i + 1].startswith("+++ "):
                new = _strip_prefix(lines[i + 1][4:])
                i += 1
            if cur is None:
                cur = _new_file_entry()
            if old is None:
                pending["added"] = True
            else:
                cur["old_path"] = old
            if new is None:
                pending["deleted"] = True
                cur["path"] = cur["path"] or old
            else:
                cur["path"] = new
            i += 1
            continue

        m = _HUNK_RE.match(line)
        if m:
            if cur is None or not cur.get("path"):
                errors.append(f"hunk without file header at line {i + 1}, skipped")
                # skip the hunk body
                i += 1
                while i < n and (lines[i][:1] in (" ", "+", "-", "\\") and not lines[i].startswith("--- ")):
                    i += 1
                continue
            old_start = int(m.group(1))
            old_count = int(m.group(2) or "1")
            new_start = int(m.group(3))
            new_count = int(m.group(4) or "1")
            hunk = {
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "lines": [],
            }
            i += 1
            old_no, new_no = old_start, new_start
            seen_old, seen_new = 0, 0
            while i < n and (seen_old < old_count or seen_new < new_count):
                body = lines[i]
                tag = body[:1]
                if tag == "\\":  # "\ No newline at end of file"
                    i += 1
                    continue
                if tag == " " or body == "":
                    hunk["lines"].append([" ", old_no, new_no, body[1:]])
                    old_no += 1
                    new_no += 1
                    seen_old += 1
                    seen_new += 1
                elif tag == "+":
                    hunk["lines"].append(["+", None, new_no, body[1:]])
                    new_no += 1
                    seen_new += 1
                elif tag == "-":
                    hunk["lines"].append(["-", old_no, None, body[1:]])
                    old_no += 1
                    seen_old += 1
                else:
                    errors.append(f"malformed hunk body at line {i + 1}: {body[:60]!r}")
                    break
                i += 1
            cur["hunks"].append(hunk)
            continue

        # resolve pending change_type markers once we are inside a file block
        if cur is not None and pending:
            if pending.get("added"):
                cur["change_type"] = "added"
            elif pending.get("deleted"):
                cur["change_type"] = "deleted"
            elif pending.get("renamed"):
                cur["change_type"] = "renamed"

        i += 1

    # final pending resolution + flush
    if cur is not None and pending:
        if pending.get("added"):
            cur["change_type"] = "added"
        elif pending.get("deleted"):
            cur["change_type"] = "deleted"
        elif pending.get("renamed"):
            cur["change_type"] = "renamed"
    flush()

    # normalize change types for files that got hunks but no explicit marker
    for f in files:
        if f["is_binary"]:
            f["change_type"] = "binary"
        elif f["change_type"] == "modified" and f["old_path"] and f["path"] \
                and f["old_path"] != f["path"]:
            f["change_type"] = "renamed"
        if f["change_type"] != "renamed" and not f["is_binary"]:
            f["old_path"] = None
    return {"files": files, "errors": errors}


def candidate_lines(file_entry: dict) -> list[int]:
    """Post-image line numbers touched by the change (added lines)."""
    out: set[int] = set()
    for hunk in file_entry.get("hunks", []):
        for tag, _old, new, _text in hunk["lines"]:
            if tag == "+" and new is not None:
                out.add(new)
    return sorted(out)


def reconstruct_post_image(file_entry: dict) -> tuple[Optional[str], bool]:
    """Rebuild post-image text from hunks alone.

    Gap lines between hunks are blank so line numbers still match the real
    post-image.  Returns ``(content, complete)`` where ``complete`` is True
    only when the hunks provably cover the whole file (a freshly added file).
    ``content`` is None for deleted/binary files or files without hunks.
    """
    if file_entry.get("is_binary") or file_entry.get("change_type") in ("deleted", "binary"):
        return None, False
    hunks = file_entry.get("hunks", [])
    if not hunks:
        return None, False
    max_line = 0
    for hunk in hunks:
        for tag, _old, new, _text in hunk["lines"]:
            if new is not None:
                max_line = max(max_line, new)
    buf: list[str] = [""] * max_line
    covered: set[int] = set()
    for hunk in hunks:
        for tag, _old, new, text in hunk["lines"]:
            if new is not None:
                buf[new - 1] = text
                covered.add(new)
    complete = (file_entry.get("change_type") == "added" and covered == set(range(1, max_line + 1)))
    return "\n".join(buf) + "\n", complete


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: parse_diff.py <input.diff> [output.json]", file=sys.stderr)
        return 2
    with open(argv[1], "r", encoding="utf-8", errors="replace") as fh:
        parsed = parse_unified_diff(fh.read())
    for f in parsed["files"]:
        f["candidate_lines"] = candidate_lines(f)
    payload = json.dumps(parsed, ensure_ascii=False, indent=2)
    if len(argv) > 2:
        with open(argv[2], "w", encoding="utf-8") as fh:
            fh.write(payload)
        print(f"parsed {len(parsed['files'])} file(s) -> {argv[2]}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
