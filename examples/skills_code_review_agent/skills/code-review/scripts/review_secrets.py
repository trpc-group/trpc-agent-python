#!/usr/bin/env python3
"""Detect sensitive values that the diff parser has already redacted."""

from review_common import ParsedDiff
from review_common import added_changes
from review_common import file_path
from review_common import finding
from review_common import run_rule_cli

RULE_NAME = "sensitive_information"


def review(parsed: ParsedDiff) -> list[dict[str, object]]:
    """Return candidates without reproducing plaintext secret evidence."""
    findings = []
    for file_data in parsed.get("files", []):
        path = file_path(file_data)
        for _hunk, change in added_changes(file_data):
            content = str(change.get("content", ""))
            if "[REDACTED" not in content:
                continue
            findings.append(
                finding(
                    severity="critical",
                    category=RULE_NAME,
                    file=path,
                    line=change.get("new_line"),
                    title="Hard-coded sensitive value",
                    evidence="A sensitive value was detected and redacted in this line.",
                    recommendation="Load the value from an approved secret provider.",
                    confidence=0.99,
                    source="skill:review_secrets.py",
                )
            )
    return findings


if __name__ == "__main__":
    raise SystemExit(run_rule_cli(RULE_NAME, review))
