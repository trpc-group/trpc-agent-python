# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Database connection / transaction lifecycle checker."""
import re
import sys

from checklib import emit, finding, iter_added_py_lines, load_files

_CONNECT_RE = re.compile(r"^\s*\w+\s*=\s*\w[\w.]*\.connect\s*\(")
_CURSOR_RE = re.compile(r"^\s*\w+\s*=\s*\w+\.cursor\s*\(")
_COMMIT_RE = re.compile(r"\.commit\s*\(")


def main():
    files = load_files(sys.argv)
    findings = []
    added_text_by_file = {}
    for path, line_no, text in iter_added_py_lines(files):
        added_text_by_file.setdefault(path, []).append(text)
        if _CONNECT_RE.match(text) and "with " not in text:
            findings.append(finding(
                "high", "db_lifecycle", path, line_no,
                "Connection opened without context manager or close()",
                evidence=text.strip(),
                recommendation="Use 'with ...connect(...) as conn:' or close the "
                               "connection in a finally block.",
                confidence=0.8))
        if _CURSOR_RE.match(text) and "with " not in text:
            findings.append(finding(
                "low", "db_lifecycle", path, line_no,
                "Cursor created without explicit lifecycle management",
                evidence=text.strip(),
                recommendation="Close the cursor or use a context manager.",
                confidence=0.5))
    for path, line_no, text in iter_added_py_lines(files):
        if _COMMIT_RE.search(text):
            all_added = "\n".join(added_text_by_file.get(path, []))
            if "rollback" not in all_added:
                findings.append(finding(
                    "medium", "db_lifecycle", path, line_no,
                    "commit() without visible rollback/error handling",
                    evidence=text.strip(),
                    recommendation="Wrap the transaction in try/except and roll back on failure.",
                    confidence=0.65))
    emit(findings)


if __name__ == "__main__":
    main()
