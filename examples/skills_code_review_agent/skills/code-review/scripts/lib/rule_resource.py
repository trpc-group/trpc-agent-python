# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Resource-leak rules (category: resource_leak)."""

from __future__ import annotations

import re
from typing import Any
from typing import Dict
from typing import List

from .rulebase import RuleContext
from .rulebase import SEVERITY_LOW
from .rulebase import SEVERITY_MEDIUM
from .rulebase import file_added_text
from .rulebase import hunk_added_text
from .rulebase import is_code_line
from .rulebase import iter_added_lines
from .rulebase import make_finding

CATEGORY = "resource_leak"

_RE_OPEN = re.compile(r"(?<![\w.])open\s*\(")
_RE_WITH_OPEN = re.compile(r"\bwith\s+[^:]*open\s*\(")
_RE_SOCKET = re.compile(r"\bsocket\.socket\s*\(")
_RE_WITH_SOCKET = re.compile(r"\bwith\s+[^:]*socket\.socket\s*\(")
_RE_TMPFILE_KEEP = re.compile(r"\bNamedTemporaryFile\s*\([^)]*delete\s*=\s*False")
_RE_THREAD_START = re.compile(r"\bthreading\.Thread\s*\(")
_RE_CLOSE = re.compile(r"\.close\s*\(")
_RE_JOIN = re.compile(r"\.join\s*\(")
_RE_ASSIGNED_OPEN = re.compile(r"^\s*(\w+)\s*=\s*open\s*\(")
_RE_ASSIGNED_SOCKET = re.compile(r"^\s*(\w+)\s*=\s*socket\.socket\s*\(")


def _closed_in_scope(ctx: RuleContext, hunk: Dict[str, Any], var: str) -> bool:
    """Best-effort: does `<var>.close()` appear anywhere in the visible change?"""
    scope_texts = [hunk_added_text(hunk), file_added_text(ctx.file_entry)]
    if ctx.content:
        scope_texts.append(ctx.content)
    needle = re.compile(rf"\b{re.escape(var)}\s*\.\s*close\s*\(")
    return any(needle.search(text) for text in scope_texts)


def check_file(ctx: RuleContext) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    path = ctx.path
    file_text = file_added_text(ctx.file_entry)

    for lineno, content, hunk in iter_added_lines(ctx.file_entry):
        if not is_code_line(content):
            continue

        match = _RE_ASSIGNED_OPEN.match(content)
        if match and not _RE_WITH_OPEN.search(content) and not _closed_in_scope(ctx, hunk, match.group(1)):
            findings.append(
                make_finding("RES001", CATEGORY, SEVERITY_MEDIUM, 0.65, path, lineno,
                             "File handle opened without context manager or close()", content,
                             "Wrap the open() in a `with` block (or ensure `.close()` runs in a "
                             "`finally`) so the handle is released on every path."))
        match = _RE_ASSIGNED_SOCKET.match(content)
        if match and not _RE_WITH_SOCKET.search(content) and not _closed_in_scope(ctx, hunk, match.group(1)):
            findings.append(
                make_finding("RES002", CATEGORY, SEVERITY_MEDIUM, 0.65, path, lineno,
                             "Socket created without context manager or close()", content,
                             "Use `with socket.socket(...) as s:` or close the socket in a "
                             "`finally` block to avoid leaking file descriptors."))
        if _RE_TMPFILE_KEEP.search(content):
            findings.append(
                make_finding("RES003", CATEGORY, SEVERITY_LOW, 0.85, path, lineno,
                             "NamedTemporaryFile(delete=False) leaves files behind", content,
                             "Delete the file explicitly (os.unlink in finally) or drop "
                             "delete=False so the OS cleans it up."))
        if (_RE_THREAD_START.search(content) and "daemon" not in content
                and not _RE_JOIN.search(file_text)):
            findings.append(
                make_finding("RES004", CATEGORY, SEVERITY_LOW, 0.5, path, lineno,
                             "Thread started without join() or daemon flag", content,
                             "join() the thread on shutdown or mark it daemon=True; otherwise it "
                             "can outlive the process teardown and leak resources."))
        if (_RE_OPEN.search(content) and "with" not in content
                and not _RE_ASSIGNED_OPEN.match(content) and "=" not in content.split("open")[0]
                and not _RE_CLOSE.search(file_text)):
            # e.g. `data = json.load(open(path))` — handle never captured at all.
            findings.append(
                make_finding("RES005", CATEGORY, SEVERITY_MEDIUM, 0.6, path, lineno,
                             "open() result used inline, handle can never be closed", content,
                             "Capture the handle in a `with open(...) as f:` block instead of "
                             "passing open() inline."))
    return findings
