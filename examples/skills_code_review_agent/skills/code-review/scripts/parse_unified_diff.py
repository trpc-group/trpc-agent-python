#!/usr/bin/env python3
"""Parse a unified diff into a small JSON structure for sandboxed review."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

MAX_DIFF_BYTES = 5 * 1024 * 1024
HUNK_PATTERN = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: (.*))?$"
)
SECRET_PATTERNS = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*\Z", re.DOTALL),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"), "sk-[REDACTED]"),
    (
        re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9_-]{8,}"),
        "[REDACTED_SERVICE_KEY]",
    ),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "AWS[REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "gh_[REDACTED]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "github_pat_[REDACTED]"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}\b"), "glpat-[REDACTED]"),
    (
        re.compile(r"\b(?:npm|hf)_[A-Za-z0-9_-]{20,}\b"),
        "[REDACTED_SERVICE_TOKEN]",
    ),
    (re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}\b"), "pypi-[REDACTED]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
        "[REDACTED_JWT]",
    ),
    (
        re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^\s:/]+:)[^\s@/]+(@)"),
        r"\1[REDACTED]\2",
    ),
    (
        re.compile(
            r"(?i)([\"']?[a-z0-9_.-]*(?:api[_-]?key|access[_-]?token|"
            r"client[_-]?secret|private[_-]?key|authorization|credential|token|"
            r"password|passwd|secret)[a-z0-9_.-]*[\"']?\s*[:=]\s*)"
            r"([\"']?)[^\s,;\"']{4,}\2"
        ),
        r"\1\2[REDACTED]\2",
    ),
    (
        re.compile(
            r"(?i)([\"']?[a-z0-9_.-]*(?:api[_-]?key|access[_-]?token|"
            r"client[_-]?secret|private[_-]?key|authorization|credential|token|"
            r"password|passwd|secret)[a-z0-9_.-]*[\"']?\s*[:=]\s*)"
            r"[\"']?[^\s,;\"']{4,}"
        ),
        r"\1[REDACTED]",
    ),
)


def _redact_text(value: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _clean_path(value: str) -> str:
    value = value.split("\t", maxsplit=1)[0]
    if value in {"/dev/null", "dev/null"}:
        return "/dev/null"
    if value.startswith(("a/", "b/")):
        return value[2:]
    return value


def _new_file(old_path: str = "", new_path: str = "") -> dict[str, Any]:
    return {
        "old_path": _clean_path(old_path),
        "new_path": _clean_path(new_path),
        "status": "modified",
        "hunks": [],
    }


def parse_unified_diff(
    diff_text: str,
    *,
    redact_sensitive: bool = True,
) -> dict[str, Any]:
    """Parse files, hunks, context, and candidate changed line numbers."""
    files: list[dict[str, Any]] = []
    current_file: dict[str, Any] | None = None
    current_hunk: dict[str, Any] | None = None
    old_line = 0
    new_line = 0
    old_consumed = 0
    new_consumed = 0
    added_lines = 0
    removed_lines = 0
    in_private_key = False

    # Track both sides independently so every emitted line keeps precise locations.
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = shlex.split(line)
            old_path = parts[2] if len(parts) > 2 else ""
            new_path = parts[3] if len(parts) > 3 else ""
            current_file = _new_file(old_path, new_path)
            files.append(current_file)
            current_hunk = None
            continue

        if line.startswith("--- ") and current_hunk is None:
            if current_file is None or current_file["hunks"]:
                current_file = _new_file()
                files.append(current_file)
            current_file["old_path"] = _clean_path(line[4:])
            continue

        if line.startswith("+++ ") and current_hunk is None:
            if current_file is None:
                current_file = _new_file()
                files.append(current_file)
            current_file["new_path"] = _clean_path(line[4:])
            old_path = current_file["old_path"]
            new_path = current_file["new_path"]
            if old_path == "/dev/null":
                current_file["status"] = "added"
            elif new_path == "/dev/null":
                current_file["status"] = "deleted"
            continue

        match = HUNK_PATTERN.match(line)
        if match and current_file is not None:
            old_line = int(match.group(1))
            new_line = int(match.group(3))
            old_consumed = 0
            new_consumed = 0
            current_hunk = {
                "old_start": old_line,
                "old_count": int(match.group(2) or 1),
                "new_start": new_line,
                "new_count": int(match.group(4) or 1),
                "context": match.group(5) or "",
                "candidate_lines": [],
                "changes": [],
            }
            current_file["hunks"].append(current_hunk)
            continue

        if current_hunk is None or line.startswith("\\ No newline"):
            continue

        prefix = line[:1]
        content = line[1:]
        # Private keys may span several diff lines; redact the whole active block.
        if redact_sensitive and re.search(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
            content,
        ):
            in_private_key = True
        if redact_sensitive and in_private_key:
            safe_content = "[REDACTED_PRIVATE_KEY]"
        else:
            safe_content = _redact_text(content) if redact_sensitive else content
        if redact_sensitive and re.search(
            r"-----END [A-Z ]*PRIVATE KEY-----",
            content,
        ):
            in_private_key = False
        if prefix == "+":
            current_hunk["candidate_lines"].append(new_line)
            current_hunk["changes"].append(
                {
                    "kind": "added",
                    "old_line": None,
                    "new_line": new_line,
                    "content": safe_content,
                }
            )
            new_line += 1
            new_consumed += 1
            added_lines += 1
        elif prefix == "-":
            current_hunk["changes"].append(
                {
                    "kind": "removed",
                    "old_line": old_line,
                    "new_line": None,
                    "content": safe_content,
                }
            )
            old_line += 1
            old_consumed += 1
            removed_lines += 1
        elif prefix == " ":
            current_hunk["changes"].append(
                {
                    "kind": "context",
                    "old_line": old_line,
                    "new_line": new_line,
                    "content": safe_content,
                }
            )
            old_line += 1
            new_line += 1
            old_consumed += 1
            new_consumed += 1

        if (
            old_consumed >= current_hunk["old_count"]
            and new_consumed >= current_hunk["new_count"]
        ):
            current_hunk = None

    return {
        "files": files,
        "summary": {
            "file_count": len(files),
            "hunk_count": sum(len(item["hunks"]) for item in files),
            "added_lines": added_lines,
            "removed_lines": removed_lines,
        },
    }


def _read_input(path: Path | None) -> str:
    if path is None:
        data = sys.stdin.buffer.read(MAX_DIFF_BYTES + 1)
    else:
        with path.open("rb") as source:
            data = source.read(MAX_DIFF_BYTES + 1)
    if len(data) > MAX_DIFF_BYTES:
        raise ValueError(f"diff exceeds {MAX_DIFF_BYTES} bytes")
    return data.decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diff_file", nargs="?", type=Path)
    args = parser.parse_args()
    try:
        result = parse_unified_diff(_read_input(args.diff_file))
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
