#!/usr/bin/env python3
"""Shared helpers for deterministic code-review Skill scripts."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from parse_unified_diff import MAX_DIFF_BYTES
from parse_unified_diff import parse_unified_diff

ParsedDiff = dict[str, Any]
Finding = dict[str, object]
Rule = Callable[[ParsedDiff], list[Finding]]


def load_diff(path: Path) -> ParsedDiff:
    """Read and parse a bounded diff with sensitive values redacted."""
    with path.open("rb") as source:
        data = source.read(MAX_DIFF_BYTES + 1)
    if len(data) > MAX_DIFF_BYTES:
        raise ValueError(f"diff exceeds {MAX_DIFF_BYTES} bytes")
    return parse_unified_diff(data.decode("utf-8", errors="replace"))


def file_path(file_data: ParsedDiff) -> str:
    """Return the effective repository-relative path for a parsed file."""
    new_path = str(file_data.get("new_path") or "")
    if new_path and new_path != "/dev/null":
        return new_path
    return str(file_data.get("old_path") or "unknown")


def added_changes(file_data: ParsedDiff):
    """Yield each added change together with its containing hunk."""
    for hunk in file_data.get("hunks", []):
        for change in hunk.get("changes", []):
            if change.get("kind") == "added":
                yield hunk, change


def current_text(file_data: ParsedDiff) -> str:
    """Join added and unchanged lines representing visible post-change code."""
    return "\n".join(
        str(change.get("content", ""))
        for hunk in file_data.get("hunks", [])
        for change in hunk.get("changes", [])
        if change.get("kind") in {"added", "context"}
    )


def finding(
    *,
    severity: str,
    category: str,
    file: str,
    line: int | None,
    title: str,
    evidence: str,
    recommendation: str,
    confidence: float,
    source: str,
) -> Finding:
    """Build the common structured finding shape."""
    return {
        "severity": severity,
        "category": category,
        "file": file,
        "line": line,
        "title": title,
        "evidence": evidence,
        "recommendation": recommendation,
        "confidence": confidence,
        "source": source,
    }


def deduplicate(items: list[Finding]) -> list[Finding]:
    """Keep the highest-confidence issue for each file, line, and category."""
    selected: dict[tuple[object, object, object], Finding] = {}
    for item in items:
        key = (item.get("file"), item.get("line"), item.get("category"))
        current = selected.get(key)
        if current is None or float(item["confidence"]) > float(current["confidence"]):
            selected[key] = item
    return list(selected.values())


def run_rule_cli(rule_name: str, rule: Rule) -> int:
    """Run one rule script against a unified diff and emit JSON."""
    parser = argparse.ArgumentParser(description=f"Run the {rule_name} review rule")
    parser.add_argument("diff_file", type=Path)
    args = parser.parse_args()
    try:
        parsed = load_diff(args.diff_file)
        result = {
            "rule": rule_name,
            "findings": deduplicate(rule(parsed)),
        }
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0
