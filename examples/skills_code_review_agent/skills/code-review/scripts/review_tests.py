#!/usr/bin/env python3
"""Identify source-only patches that may need focused regression tests."""

from pathlib import PurePosixPath

from review_common import ParsedDiff
from review_common import file_path
from review_common import finding
from review_common import run_rule_cli

RULE_NAME = "test_missing"
SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".ts",
    ".tsx",
}


def _is_test(path: str) -> bool:
    lowered = path.lower()
    name = PurePosixPath(lowered).name
    return (
        "/test/" in f"/{lowered}/"
        or "/tests/" in f"/{lowered}/"
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
        or name.endswith("_test.py")
        or name.endswith("_test.go")
    )


def _normalized_changes(file_data: ParsedDiff, kind: str) -> list[str]:
    lines = []
    for hunk in file_data.get("hunks", []):
        for change in hunk.get("changes", []):
            if change.get("kind") != kind:
                continue
            content = str(change.get("content", "")).strip()
            if not content or content.startswith(("#", "//")):
                continue
            lines.append("".join(content.split()))
    return lines


def _has_material_change(file_data: ParsedDiff) -> bool:
    return _normalized_changes(file_data, "added") != _normalized_changes(
        file_data,
        "removed",
    )


def review(parsed: ParsedDiff) -> list[dict[str, object]]:
    """Return one low-confidence candidate when source changes lack test changes."""
    files = [(file_path(item), item) for item in parsed.get("files", [])]
    paths = [path for path, _item in files]
    source_paths = [
        path
        for path, item in files
        if PurePosixPath(path).suffix.lower() in SOURCE_SUFFIXES
        and not _is_test(path)
        and _has_material_change(item)
    ]
    if not source_paths or any(_is_test(path) for path in paths):
        return []
    return [
        finding(
            severity="medium",
            category=RULE_NAME,
            file=source_paths[0],
            line=None,
            title="Behavioral source changes have no focused test change",
            evidence="The patch changes source files but no test file.",
            recommendation="Add a focused regression test for the changed behavior.",
            confidence=0.65,
            source="skill:review_tests.py",
        )
    ]


if __name__ == "__main__":
    raise SystemExit(run_rule_cli(RULE_NAME, review))
