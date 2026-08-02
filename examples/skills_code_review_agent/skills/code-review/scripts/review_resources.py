#!/usr/bin/env python3
"""Detect added resources without deterministic cleanup."""

import re

from review_common import ParsedDiff
from review_common import added_changes
from review_common import current_text
from review_common import file_path
from review_common import finding
from review_common import run_rule_cli

RULE_NAME = "resource_leak"


def _managed(name: str, text: str, methods: str) -> bool:
    return bool(
        re.search(
            rf"\b{re.escape(name)}\.(?:{methods})\s*\(",
            text,
            re.IGNORECASE,
        )
    )


def review(parsed: ParsedDiff) -> list[dict[str, object]]:
    """Return deterministic file, process, socket, and lock lifecycle candidates."""
    findings = []
    patterns = (
        (r"^\s*(\w+)\s*=\s*open\s*\(", "close|aclose", "file handle"),
        (r"^\s*(\w+)\s*=\s*socket\.socket\s*\(", "close", "socket"),
        (
            r"^\s*(\w+)\s*=\s*subprocess\.popen\s*\(",
            "wait|communicate|terminate|kill",
            "child process",
        ),
    )
    for file_data in parsed.get("files", []):
        path = file_path(file_data)
        text = current_text(file_data)
        for _hunk, change in added_changes(file_data):
            content = str(change.get("content", ""))
            lowered = content.lower()
            if re.match(r"^\s*(?:async\s+)?with\b", lowered):
                continue
            for pattern, cleanup, resource_name in patterns:
                match = re.search(pattern, lowered, re.IGNORECASE)
                if not match or _managed(match.group(1), text, cleanup):
                    continue
                findings.append(
                    finding(
                        severity="medium",
                        category=RULE_NAME,
                        file=path,
                        line=change.get("new_line"),
                        title=f"{resource_name.title()} lacks deterministic cleanup",
                        evidence=content,
                        recommendation=(
                            "Use a context manager or guaranteed cleanup in a finally block."
                        ),
                        confidence=0.86,
                        source="skill:review_resources.py",
                    )
                )
                break
            acquire = re.search(r"\b(\w+)\.acquire\s*\(", content)
            if acquire and not _managed(acquire.group(1), text, "release"):
                findings.append(
                    finding(
                        severity="high",
                        category=RULE_NAME,
                        file=path,
                        line=change.get("new_line"),
                        title="Lock acquisition has no matching release",
                        evidence=content,
                        recommendation="Use a context manager or release the lock in finally.",
                        confidence=0.84,
                        source="skill:review_resources.py",
                    )
                )
    return findings


if __name__ == "__main__":
    raise SystemExit(run_rule_cli(RULE_NAME, review))
