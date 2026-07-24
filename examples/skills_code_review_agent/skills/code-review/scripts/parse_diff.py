#!/usr/bin/env python3
"""Parse a unified diff file and output structured change information.

Usage:
    python parse_diff.py <diff_file> <output_file>

Output:
    JSON file with keys: files, total_additions, total_deletions, files_changed
"""

import json
import re
import sys
from pathlib import Path
from typing import Any


def parse_diff(diff_path: str) -> dict[str, Any]:
    """Parse a unified diff file into structured change information."""
    content = Path(diff_path).read_text(encoding="utf-8")
    files: list[dict[str, Any]] = []
    current_file: dict[str, Any] | None = None
    total_additions = 0
    total_deletions = 0

    for line in content.splitlines():
        if line.startswith("+++ b/"):
            if current_file:
                files.append(current_file)
            current_file = {
                "path": line[6:],
                "change_type": "modified",
                "additions": 0,
                "deletions": 0,
                "hunks": [],
            }
            continue

        hunk_match = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)", line)
        if hunk_match and current_file is not None:
            hunk = {
                "start_line": int(hunk_match.group(2)),
                "content": line,
                "added_lines": [],
                "deleted_lines": [],
            }
            current_file["hunks"].append(hunk)
            continue

        if line.startswith("+") and not line.startswith("+++"):
            total_additions += 1
            if current_file and current_file["hunks"]:
                current_file["additions"] += 1
        elif line.startswith("-") and not line.startswith("---"):
            total_deletions += 1
            if current_file:
                current_file["deletions"] += 1

    if current_file:
        files.append(current_file)

    return {
        "files": files,
        "total_additions": total_additions,
        "total_deletions": total_deletions,
        "files_changed": len(files),
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python parse_diff.py <diff_file> <output_file>", file=sys.stderr)
        sys.exit(1)

    diff_file = sys.argv[1]
    output_file = sys.argv[2]

    result = parse_diff(diff_file)
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    Path(output_file).write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Parsed diff: {result['files_changed']} files, "
          f"{result['total_additions']} additions, {result['total_deletions']} deletions")