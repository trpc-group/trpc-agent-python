#!/usr/bin/env python3
"""Parse one unified diff and run all deterministic review rule scripts."""

import argparse
import json
import sys
from pathlib import Path

import review_async
import review_database
import review_resources
import review_secrets
import review_security
import review_tests
from review_common import deduplicate
from review_common import load_diff

RULES = (
    review_security.review,
    review_async.review,
    review_resources.review,
    review_database.review,
    review_tests.review,
    review_secrets.review,
)
MAX_PAGE_SIZE = 24
MAX_RECORD_TEXT = 320


def run_all(parsed: dict[str, object]) -> list[dict[str, object]]:
    """Run every rule and deduplicate their structured candidates."""
    findings = []
    for rule in RULES:
        findings.extend(rule(parsed))
    return deduplicate(findings)


def _bounded(value: object) -> str:
    text = "".join(
        character if character in {"\n", "\t"} or ord(character) >= 32 else "�"
        for character in str(value)
    )
    if len(text) <= MAX_RECORD_TEXT:
        return text
    return text[: MAX_RECORD_TEXT - 1] + "…"


def _records(
    parsed: dict[str, object],
    findings: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Flatten candidates and changed-line evidence into bounded page records."""
    records: list[dict[str, object]] = []
    for finding_data in findings:
        records.append(
            {
                "type": "finding",
                **{
                    key: _bounded(value) if isinstance(value, str) else value
                    for key, value in finding_data.items()
                },
            }
        )
    for file_data in parsed["files"]:
        path = file_data.get("new_path") or file_data.get("old_path") or "unknown"
        if path == "/dev/null":
            path = file_data.get("old_path") or "unknown"
        for hunk in file_data.get("hunks", []):
            for change in hunk.get("changes", []):
                records.append(
                    {
                        "type": "change",
                        "file": _bounded(path),
                        "status": file_data.get("status", "modified"),
                        "hunk": _bounded(hunk.get("context", "")),
                        "kind": change.get("kind"),
                        "old_line": change.get("old_line"),
                        "new_line": change.get("new_line"),
                        "content": _bounded(change.get("content", "")),
                    }
                )
    return records


def build_page(
    parsed: dict[str, object],
    *,
    cursor: int = 0,
    limit: int = MAX_PAGE_SIZE,
) -> dict[str, object]:
    """Build a JSON page that stays below the SDK's inline output ceiling."""
    if cursor < 0:
        raise ValueError("cursor must not be negative")
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    findings = run_all(parsed)
    records = _records(parsed, findings)
    end = min(len(records), cursor + limit)
    return {
        "summary": parsed["summary"],
        "cursor": cursor,
        "next_cursor": end if end < len(records) else None,
        "total_records": len(records),
        "finding_count": len(findings),
        "records": records[cursor:end],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diff_file", type=Path)
    parser.add_argument("--cursor", type=int, default=0)
    parser.add_argument("--limit", type=int, default=MAX_PAGE_SIZE)
    args = parser.parse_args()
    try:
        parsed = load_diff(args.diff_file)
        result = build_page(parsed, cursor=args.cursor, limit=args.limit)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
