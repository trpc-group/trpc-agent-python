#!/usr/bin/env python3
"""Parse a unified diff and write a compact JSON summary."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


HUNK_RE = re.compile(r"@@ -(?P<old>\d+)(?:,(?P<old_count>\d+))? \+(?P<new>\d+)(?:,(?P<new_count>\d+))? @@")


def normalize(path: str) -> str:
    path = path.strip()
    if path in {"/dev/null", "dev/null"}:
        return ""
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path


def parse(diff_text: str) -> dict:
    files = []
    current = None
    old_path = ""
    old_line = 0
    new_line = 0
    for raw in diff_text.replace("\r\n", "\n").splitlines():
        if raw.startswith("--- "):
            old_path = normalize(raw[4:].split("\t", 1)[0])
            continue
        if raw.startswith("+++ "):
            current = {"path": normalize(raw[4:].split("\t", 1)[0]) or old_path, "added_lines": 0, "deleted_lines": 0, "hunks": 0}
            files.append(current)
            continue
        match = HUNK_RE.match(raw)
        if match and current is not None:
            current["hunks"] += 1
            old_line = int(match.group("old"))
            new_line = int(match.group("new"))
            continue
        if current is None:
            continue
        if raw.startswith("+") and not raw.startswith("+++ "):
            current["added_lines"] += 1
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("--- "):
            current["deleted_lines"] += 1
            old_line += 1
        elif raw.startswith(" "):
            old_line += 1
            new_line += 1
    return {
        "file_count": len(files),
        "added_lines": sum(item["added_lines"] for item in files),
        "deleted_lines": sum(item["deleted_lines"] for item in files),
        "files": files,
    }


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: parse_diff.py INPUT.diff OUTPUT.json", file=sys.stderr)
        return 2
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    payload = parse(input_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

