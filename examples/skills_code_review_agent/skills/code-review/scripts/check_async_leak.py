# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Async/resource leak checker for added lines."""
import re
import sys

from checklib import emit, finding, iter_added_py_lines, load_files

_SESSION_RE = re.compile(r"aiohttp\.ClientSession\s*\(")
_CREATE_TASK_RE = re.compile(r"^\s*asyncio\.create_task\s*\(")
_OPEN_ASSIGN_RE = re.compile(r"^\s*\w+\s*=\s*open\s*\(")


def main():
    files = load_files(sys.argv)
    findings = []
    for path, line_no, text in iter_added_py_lines(files):
        if _SESSION_RE.search(text) and "async with" not in text:
            findings.append(finding(
                "high", "async_resource_leak", path, line_no,
                "ClientSession created without async context manager",
                evidence=text.strip(),
                recommendation="Use 'async with aiohttp.ClientSession() as session:' "
                               "or ensure session.close() is awaited.",
                confidence=0.8))
        if _CREATE_TASK_RE.match(text):
            findings.append(finding(
                "medium", "async_resource_leak", path, line_no,
                "Fire-and-forget asyncio.create_task without keeping a reference",
                evidence=text.strip(),
                recommendation="Store the task and await/cancel it; unreferenced "
                               "tasks may be garbage collected mid-flight.",
                confidence=0.7))
        if _OPEN_ASSIGN_RE.match(text) and "with " not in text:
            findings.append(finding(
                "medium", "async_resource_leak", path, line_no,
                "File opened without context manager",
                evidence=text.strip(),
                recommendation="Use 'with open(...) as f:' so the handle is always closed.",
                confidence=0.7))
    emit(findings)


if __name__ == "__main__":
    main()
