# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unified diff parser (stdlib-only, shared by sandbox and host).

Parses ``git diff`` / plain unified diff text into a normalized changeset
dict with files, hunks, per-line old/new line numbers, context lines and
candidate (added) line numbers — exactly the structure the review rules
consume.
"""

from __future__ import annotations

import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

_RE_DIFF_GIT = re.compile(r'^diff --git (?:"?a/(?P<a>.+?)"?) (?:"?b/(?P<b>.+?)"?)\s*$')
_RE_HUNK = re.compile(r"^@@ -(?P<os>\d+)(?:,(?P<oc>\d+))? \+(?P<ns>\d+)(?:,(?P<nc>\d+))? @@(?P<hdr>.*)$")
_RE_OLD_FILE = re.compile(r'^--- (?:"?a/(?P<p>.+?)"?|(?P<devnull>/dev/null))\s*$')
_RE_NEW_FILE = re.compile(r'^\+\+\+ (?:"?b/(?P<p>.+?)"?|(?P<devnull>/dev/null))\s*$')

STATUS_ADDED = "added"
STATUS_DELETED = "deleted"
STATUS_MODIFIED = "modified"
STATUS_RENAMED = "renamed"


def _new_file_entry() -> Dict[str, Any]:
    return {
        "path": "",
        "old_path": "",
        "status": STATUS_MODIFIED,
        "is_binary": False,
        "hunks": [],
        "added_lines": [],
        "removed_lines": [],
    }


def _finish_file(files: List[Dict[str, Any]], entry: Optional[Dict[str, Any]]) -> None:
    if entry is None:
        return
    if not entry["path"] and not entry["old_path"]:
        return
    if not entry["path"]:
        # File deleted: keep the old path so findings can still reference it.
        entry["path"] = entry["old_path"]
    for hunk in entry["hunks"]:
        for line in hunk["lines"]:
            if line["tag"] == "+":
                entry["added_lines"].append({"line": line["new_lineno"], "content": line["content"]})
            elif line["tag"] == "-":
                entry["removed_lines"].append({"line": line["old_lineno"], "content": line["content"]})
    files.append(entry)


def parse_unified_diff(text: str) -> Dict[str, Any]:
    """Parse unified diff text into a normalized changeset dict.

    Args:
        text: Raw unified diff text (``git diff`` output or plain diff).

    Returns:
        ``{"files": [...]}`` where every file entry carries path, old_path,
        status, hunks (with per-line old/new line numbers and context lines)
        and flattened ``added_lines`` / ``removed_lines`` candidates.
    """
    files: List[Dict[str, Any]] = []
    entry: Optional[Dict[str, Any]] = None
    hunk: Optional[Dict[str, Any]] = None
    old_lineno = new_lineno = 0

    for raw_line in text.splitlines():
        m = _RE_DIFF_GIT.match(raw_line)
        if m:
            _finish_file(files, entry)
            entry = _new_file_entry()
            entry["old_path"] = m.group("a")
            entry["path"] = m.group("b")
            hunk = None
            continue

        if raw_line.startswith("new file mode"):
            if entry is not None:
                entry["status"] = STATUS_ADDED
            continue
        if raw_line.startswith("deleted file mode"):
            if entry is not None:
                entry["status"] = STATUS_DELETED
            continue
        if raw_line.startswith("rename from "):
            if entry is not None:
                entry["status"] = STATUS_RENAMED
                entry["old_path"] = raw_line[len("rename from "):].strip()
            continue
        if raw_line.startswith("rename to "):
            if entry is not None:
                entry["status"] = STATUS_RENAMED
                entry["path"] = raw_line[len("rename to "):].strip()
            continue
        if raw_line.startswith("Binary files ") or raw_line.startswith("GIT binary patch"):
            if entry is not None:
                entry["is_binary"] = True
            continue
        if raw_line.startswith("index ") or raw_line.startswith("similarity index ") \
                or raw_line.startswith("old mode") or raw_line.startswith("new mode"):
            continue

        m = _RE_OLD_FILE.match(raw_line)
        if m and hunk is None:
            if entry is None:
                # Plain unified diff without a "diff --git" header.
                entry = _new_file_entry()
            if m.group("devnull"):
                entry["status"] = STATUS_ADDED
            else:
                entry["old_path"] = m.group("p")
            continue

        m = _RE_NEW_FILE.match(raw_line)
        if m and hunk is None:
            if entry is None:
                entry = _new_file_entry()
            if m.group("devnull"):
                entry["status"] = STATUS_DELETED
            else:
                entry["path"] = m.group("p")
            continue

        m = _RE_HUNK.match(raw_line)
        if m:
            if entry is None:
                entry = _new_file_entry()
            old_lineno = int(m.group("os"))
            new_lineno = int(m.group("ns"))
            hunk = {
                "old_start": old_lineno,
                "old_count": int(m.group("oc") or "1"),
                "new_start": new_lineno,
                "new_count": int(m.group("nc") or "1"),
                "header": m.group("hdr").strip(),
                "lines": [],
            }
            entry["hunks"].append(hunk)
            continue

        if hunk is None:
            continue
        if raw_line.startswith("\\"):
            # "\ No newline at end of file"
            continue
        if raw_line.startswith("+"):
            hunk["lines"].append({
                "tag": "+",
                "old_lineno": None,
                "new_lineno": new_lineno,
                "content": raw_line[1:],
            })
            new_lineno += 1
        elif raw_line.startswith("-"):
            hunk["lines"].append({
                "tag": "-",
                "old_lineno": old_lineno,
                "new_lineno": None,
                "content": raw_line[1:],
            })
            old_lineno += 1
        else:
            # Context line ("" happens for empty context lines in some diffs).
            content = raw_line[1:] if raw_line.startswith(" ") else raw_line
            hunk["lines"].append({
                "tag": " ",
                "old_lineno": old_lineno,
                "new_lineno": new_lineno,
                "content": content,
            })
            old_lineno += 1
            new_lineno += 1

    _finish_file(files, entry)
    return {"files": files}


def build_diff_summary(changeset: Dict[str, Any]) -> Dict[str, Any]:
    """Build a content-free summary of a parsed changeset.

    Deliberately excludes raw line contents so the summary can be stored
    in the database without any secret-leak surface.
    """
    files_summary = []
    total_added = total_removed = total_hunks = 0
    for f in changeset.get("files", []):
        added = len(f.get("added_lines", []))
        removed = len(f.get("removed_lines", []))
        hunks = f.get("hunks", [])
        total_added += added
        total_removed += removed
        total_hunks += len(hunks)
        files_summary.append({
            "path": f.get("path", ""),
            "old_path": f.get("old_path", ""),
            "status": f.get("status", ""),
            "is_binary": bool(f.get("is_binary")),
            "hunk_count": len(hunks),
            "added_count": added,
            "removed_count": removed,
            "hunk_ranges": [[h.get("new_start"), h.get("new_count")] for h in hunks],
            "candidate_lines": [line["line"] for line in f.get("added_lines", [])],
        })
    return {
        "file_count": len(files_summary),
        "hunk_count": total_hunks,
        "added_line_count": total_added,
        "removed_line_count": total_removed,
        "files": files_summary,
    }
