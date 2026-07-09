#!/usr/bin/env python3
"""Parse unified diff into structured JSON output.

Usage: python parse_diff.py <diff_file>
       git diff | python parse_diff.py --stdin
"""

import argparse
import json
import re
import sys


def parse_unified_diff(diff_text: str) -> list[dict]:
    """Parse unified diff text into structured file/hunk objects."""
    files: list[dict] = []
    current_file: dict | None = None

    file_header_re = re.compile(r"^diff --git a/(.+) b/(.+)")
    hunk_header_re = re.compile(r"^@@ -(\d+),?\d* \+(\d+),?\d* @@")

    for line in diff_text.split("\n"):
        m = file_header_re.match(line)
        if m:
            current_file = {
                "filename": m.group(2),
                "old_filename": m.group(1),
                "hunks": [],
            }
            files.append(current_file)
            continue

        if current_file is None:
            continue

        m = hunk_header_re.match(line)
        if m:
            current_file["hunks"].append({
                "old_start": int(m.group(1)),
                "new_start": int(m.group(2)),
                "lines": [],
            })
            continue

        if current_file["hunks"]:
            hunk = current_file["hunks"][-1]
            if line.startswith("+"):
                hunk["lines"].append({"type": "added", "content": line[1:]})
            elif line.startswith("-"):
                hunk["lines"].append({"type": "removed", "content": line[1:]})
            elif line.startswith(" "):
                hunk["lines"].append({"type": "context", "content": line[1:]})

    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse unified diff to JSON")
    parser.add_argument("diff_file", nargs="?", help="Path to diff file")
    parser.add_argument("--stdin", action="store_true", help="Read diff from stdin")
    args = parser.parse_args()

    if args.stdin:
        diff_text = sys.stdin.read()
    elif args.diff_file:
        with open(args.diff_file, "r", encoding="utf-8") as f:
            diff_text = f.read()
    else:
        parser.print_help()
        return 1

    files = parse_unified_diff(diff_text)
    json.dump(files, sys.stdout, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
