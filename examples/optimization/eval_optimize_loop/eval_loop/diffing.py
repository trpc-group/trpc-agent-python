"""Prompt diff helpers."""

from __future__ import annotations

import difflib


def make_unified_diff(before: str, after: str, *, before_name: str, after_name: str) -> str:
    """Return a stable unified diff for prompt text."""

    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=before_name,
        tofile=after_name,
        lineterm="",
    )
    rendered = "\n".join(line.rstrip() for line in diff)
    return rendered or f"--- {before_name}\n+++ {after_name}\n# no prompt changes"
