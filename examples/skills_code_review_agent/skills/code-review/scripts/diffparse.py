# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unified diff parser shared by all code-review checker scripts (stdlib only)."""
import re

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _strip_prefix(path):
    """Strip git's a/ b/ prefixes."""
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def parse_unified_diff(text):
    """Parse a unified diff into a list of per-file dicts with new-file line numbers."""
    files = []
    current = None
    old_no = new_no = 0
    for line in text.splitlines():
        if line.startswith("diff --git") or line.startswith("Index: "):
            current = None
            continue
        if line.startswith("--- "):
            raw_old = line[4:].split("\t")[0].strip()
            current = {
                "path": "",
                "old_path": "" if raw_old == "/dev/null" else _strip_prefix(raw_old),
                "is_new": raw_old == "/dev/null",
                "is_deleted": False,
                "hunks": [],
                "added_lines": [],
                "removed_lines": [],
            }
            files.append(current)
            continue
        if line.startswith("+++ "):
            if current is None:
                continue
            raw_new = line[4:].split("\t")[0].strip()
            if raw_new == "/dev/null":
                current["is_deleted"] = True
                current["path"] = current["old_path"]
            else:
                current["path"] = _strip_prefix(raw_new)
            continue
        m = _HUNK_RE.match(line)
        if m and current is not None:
            old_no, new_no = int(m.group(1)), int(m.group(3))
            current["hunks"].append({"old_start": old_no, "new_start": new_no, "lines": []})
            continue
        if current is None or not current["hunks"]:
            continue
        hunk = current["hunks"][-1]
        if line.startswith("+"):
            hunk["lines"].append({"tag": "+", "new_lineno": new_no, "old_lineno": None, "text": line[1:]})
            current["added_lines"].append({"line": new_no, "text": line[1:]})
            new_no += 1
        elif line.startswith("-"):
            hunk["lines"].append({"tag": "-", "new_lineno": None, "old_lineno": old_no, "text": line[1:]})
            current["removed_lines"].append({"line": old_no, "text": line[1:]})
            old_no += 1
        elif line.startswith(" ") or line == "":
            hunk["lines"].append({"tag": " ", "new_lineno": new_no, "old_lineno": old_no, "text": line[1:]})
            old_no += 1
            new_no += 1
        # "\ No newline at end of file" markers are intentionally ignored.
    return files


def summarize(files):
    """Return a compact summary dict for a parsed diff."""
    return {
        "files_changed": len(files),
        "additions": sum(len(f["added_lines"]) for f in files),
        "deletions": sum(len(f["removed_lines"]) for f in files),
        "files": [f["path"] or f["old_path"] for f in files],
    }
