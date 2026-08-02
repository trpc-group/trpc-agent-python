#!/usr/bin/env python3
"""Detect detached tasks and blocking calls added to async code."""

import re

from review_common import ParsedDiff
from review_common import added_changes
from review_common import current_text
from review_common import file_path
from review_common import finding
from review_common import run_rule_cli

RULE_NAME = "async_error"
UNAWAITED_STANDALONE_CALL = re.compile(
    r"^\s*asyncio\.(?:sleep|gather|wait|wait_for|to_thread)\s*\(",
    re.IGNORECASE,
)


def _is_task_managed(name: str | None, text: str) -> bool:
    if not name:
        return False
    escaped = re.escape(name)
    return bool(
        re.search(rf"\bawait\s+{escaped}\b", text)
        or re.search(rf"\b(?:gather|wait)\s*\([^)]*\b{escaped}\b", text)
        or re.search(rf"\b{escaped}\.add_done_callback\s*\(", text)
    )


def review(parsed: ParsedDiff) -> list[dict[str, object]]:
    """Return deterministic async correctness candidates."""
    findings = []
    for file_data in parsed.get("files", []):
        path = file_path(file_data)
        text = current_text(file_data)
        has_async_def = "async def " in text
        for hunk, change in added_changes(file_data):
            content = str(change.get("content", ""))
            match = re.search(
                r"(?:(\w+)\s*=\s*)?asyncio\.(?:create_task|ensure_future)\s*\(",
                content,
            )
            if match and not _is_task_managed(match.group(1), text):
                findings.append(
                    finding(
                        severity="high",
                        category=RULE_NAME,
                        file=path,
                        line=change.get("new_line"),
                        title="Asynchronous task is detached from its lifecycle",
                        evidence=content,
                        recommendation=(
                            "Track and await the task, or manage it with a task group."
                        ),
                        confidence=0.91,
                        source="skill:review_async.py",
                    )
                )
                continue
            async_context = has_async_def or "async " in str(hunk.get("context", ""))
            if async_context and UNAWAITED_STANDALONE_CALL.search(content):
                findings.append(
                    finding(
                        severity="high",
                        category=RULE_NAME,
                        file=path,
                        line=change.get("new_line"),
                        title="Coroutine call is missing await",
                        evidence=content,
                        recommendation="Await the coroutine or explicitly manage its task.",
                        confidence=0.94,
                        source="skill:review_async.py",
                    )
                )
                continue
            if async_context and re.search(
                r"\b(?:time\.sleep|requests\.(?:get|post|put|delete))\s*\(",
                content,
            ):
                findings.append(
                    finding(
                        severity="medium",
                        category=RULE_NAME,
                        file=path,
                        line=change.get("new_line"),
                        title="Blocking operation was added to an async path",
                        evidence=content,
                        recommendation="Use an async API or isolate blocking work in an executor.",
                        confidence=0.82,
                        source="skill:review_async.py",
                    )
                )
    return findings


if __name__ == "__main__":
    raise SystemExit(run_rule_cli(RULE_NAME, review))
