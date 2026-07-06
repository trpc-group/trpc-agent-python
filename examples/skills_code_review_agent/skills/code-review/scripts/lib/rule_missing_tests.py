# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Missing-tests rule (category: missing_tests) — changeset level."""

from __future__ import annotations

from typing import Any
from typing import Dict
from typing import List

from .rulebase import SEVERITY_LOW
from .rulebase import make_finding

CATEGORY = "missing_tests"

_CODE_SUFFIXES = (".py", ".go", ".js", ".ts", ".java", ".cc", ".cpp", ".rs")
_DOC_SUFFIXES = (".md", ".rst", ".txt", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".json", ".lock")


def _is_test_path(path: str) -> bool:
    lowered = path.lower().replace("\\", "/")
    name = lowered.rsplit("/", 1)[-1]
    return ("tests/" in lowered or lowered.startswith("test/") or "/test/" in lowered
            or name.startswith("test_") or name.endswith("_test.py") or name.endswith(".test.js")
            or name.endswith(".test.ts") or name.endswith("_test.go"))


def _is_code_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(_CODE_SUFFIXES) and not lowered.endswith(_DOC_SUFFIXES)


def check_changeset(changeset: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One changeset-level finding when code changed but no tests did."""
    code_files: List[str] = []
    has_test_change = False
    for entry in changeset.get("files", []):
        path = entry.get("path", "")
        if not path or entry.get("is_binary") or entry.get("status") == "deleted":
            continue
        if not entry.get("added_lines"):
            continue
        if _is_test_path(path):
            has_test_change = True
        elif _is_code_path(path):
            code_files.append(path)

    if not code_files or has_test_change:
        return []

    file_list = ", ".join(code_files[:5]) + ("…" if len(code_files) > 5 else "")
    return [
        make_finding("TST001", CATEGORY, SEVERITY_LOW, 0.9, code_files[0], 1,
                     "Code changed without accompanying test changes",
                     f"Changed code files with no test updates: {file_list}",
                     "Add or update unit tests covering the changed behavior (test_*.py / "
                     "*_test.py under tests/); untested changes regress silently.")
    ]
