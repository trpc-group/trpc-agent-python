# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Async-error rules (category: async_error)."""

from __future__ import annotations

import re
from typing import Any
from typing import Dict
from typing import List

from .rulebase import RuleContext
from .rulebase import SEVERITY_HIGH
from .rulebase import SEVERITY_MEDIUM
from .rulebase import enclosing_def_is_async
from .rulebase import is_code_line
from .rulebase import iter_added_lines
from .rulebase import make_finding

CATEGORY = "async_error"

_RE_TIME_SLEEP = re.compile(r"\btime\.sleep\s*\(")
_RE_BLOCKING_IO = re.compile(r"\b(?:requests|urllib\.request)\.\w+\s*\(")
_RE_CREATE_TASK_DISCARDED = re.compile(r"^\s*asyncio\.(?:create_task|ensure_future)\s*\(")
_RE_BARE_CORO_CALL = re.compile(r"^\s*(?:await\s+)?(\w+(?:\.\w+)*)\s*\(")
_RE_ASYNC_DEF = re.compile(r"^\s*async\s+def\s+(\w+)")
_RE_SUBPROCESS_BLOCKING = re.compile(r"\bsubprocess\.(?:run|call|check_output|check_call)\s*\(")


def _collect_async_def_names(ctx: RuleContext) -> List[str]:
    """Names of coroutine functions defined in the visible change/content."""
    names: List[str] = []
    sources: List[str] = []
    if ctx.content_lines:
        sources.extend(ctx.content_lines)
    else:
        for hunk in ctx.file_entry.get("hunks", []):
            for line in hunk.get("lines", []):
                if line.get("tag") in ("+", " "):
                    sources.append(line.get("content", ""))
    for content in sources:
        match = _RE_ASYNC_DEF.match(content)
        if match:
            names.append(match.group(1))
    return names


def check_file(ctx: RuleContext) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    path = ctx.path
    async_def_names = _collect_async_def_names(ctx)

    for lineno, content, hunk in iter_added_lines(ctx.file_entry):
        if not is_code_line(content):
            continue
        inside_async = enclosing_def_is_async(ctx, hunk, lineno, content)

        if inside_async and _RE_TIME_SLEEP.search(content):
            findings.append(
                make_finding("ASY001", CATEGORY, SEVERITY_HIGH, 0.9, path, lineno,
                             "Blocking time.sleep inside async function", content,
                             "Use `await asyncio.sleep(...)` inside coroutines; time.sleep blocks "
                             "the whole event loop."))
        if inside_async and _RE_BLOCKING_IO.search(content):
            findings.append(
                make_finding("ASY002", CATEGORY, SEVERITY_MEDIUM, 0.75, path, lineno,
                             "Blocking network IO inside async function", content,
                             "Use an async HTTP client (aiohttp/httpx.AsyncClient) or run the "
                             "blocking call in `asyncio.to_thread(...)`."))
        if inside_async and _RE_SUBPROCESS_BLOCKING.search(content):
            findings.append(
                make_finding("ASY005", CATEGORY, SEVERITY_MEDIUM, 0.7, path, lineno,
                             "Blocking subprocess call inside async function", content,
                             "Use `asyncio.create_subprocess_exec(...)` or wrap the call in "
                             "`asyncio.to_thread(...)`."))
        if _RE_CREATE_TASK_DISCARDED.match(content):
            findings.append(
                make_finding("ASY003", CATEGORY, SEVERITY_MEDIUM, 0.6, path, lineno,
                             "asyncio task created but reference discarded", content,
                             "Keep a reference to the task (and await or gather it); discarded "
                             "tasks can be garbage-collected mid-run and swallow exceptions."))
        match = _RE_BARE_CORO_CALL.match(content)
        if (match and async_def_names and "await" not in content and "return" not in content
                and match.group(1).split(".")[-1] in async_def_names
                and not _RE_CREATE_TASK_DISCARDED.match(content)):
            findings.append(
                make_finding("ASY004", CATEGORY, SEVERITY_HIGH, 0.6, path, lineno,
                             "Coroutine called without await", content,
                             f"`{match.group(1)}` is defined as `async def`; call it with "
                             "`await` (or schedule it with asyncio.create_task and keep the "
                             "reference), otherwise it never runs."))
    return findings
