# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Map what the code-review Skill produced back into the example's own types.

The skill runs in a sandbox and speaks only ``docs/OUTPUT_SCHEMA.md`` — a JSON envelope plus a
``skill_run`` result dict. This module is the single translation point between that wire format and
``pipeline.types``, so nothing downstream needs to know whether the skill ran through the framework's
``skill_run`` tool or through the development subprocess runner.
"""
from __future__ import annotations

from typing import Any

from .types import Finding, SandboxRunResult

#: Fields the schema marks mandatory; a row missing any of them is not a usable finding.
_REQUIRED = ("severity", "category", "title", "evidence", "recommendation", "confidence", "source")

_SCRIPT_NAME = "run_checks.py"


def findings_from_payload(payload: dict[str, Any]) -> list[Finding]:
    """Map the skill's ``findings.json`` rows into ``Finding`` objects.

    A malformed row is dropped rather than sinking the review. ``status`` and ``rule_id`` are
    carried through when present: the skill sets ``status`` only for scanner errors, where
    ``needs_human_review`` is inherent and must survive the dedup stage's confidence routing.
    """
    out: list[Finding] = []
    for row in payload.get("findings", []) or []:
        if not isinstance(row, dict) or any(row.get(k) is None for k in _REQUIRED):
            continue
        try:
            finding = Finding(severity=row["severity"],
                              category=row["category"],
                              file=row.get("file", ""),
                              line=row.get("line"),
                              title=row["title"],
                              evidence=row["evidence"],
                              recommendation=row["recommendation"],
                              confidence=float(row["confidence"]),
                              source=row["source"],
                              rule_id=row.get("rule_id"))
        except Exception:  # noqa: BLE001 - boundary: one bad row must not fail the review
            continue
        if row.get("status"):
            finding.status = row["status"]
        out.append(finding)
    return out


def tool_calls_from_payload(payload: dict[str, Any]) -> int:
    """How many scanners actually ran *inside the sandbox* (the envelope's own count).

    Measured where the work happened, so it cannot drift from the host's PATH the way a host-side
    ``shutil.which`` sweep did.
    """
    declared = payload.get("tool_calls")
    if isinstance(declared, int):
        return declared
    tools = payload.get("tools")
    return sum(1 for ran in tools.values() if ran) if isinstance(tools, dict) else 0


def sandbox_run_from_skill_result(result: dict[str, Any], *, max_bytes: int = 1 << 20) -> SandboxRunResult:
    """Map a ``skill_run`` tool result into the report's sandbox-execution summary.

    Without this the report's sandbox section and the ``sandbox_runs`` table would be permanently
    empty on the agent path — the framework's sandbox would be doing real work that the audit trail
    never recorded.
    """
    stdout_bytes = len((result.get("stdout") or "").encode("utf-8", "replace"))
    stderr_bytes = len((result.get("stderr") or "").encode("utf-8", "replace"))
    return SandboxRunResult(script=_SCRIPT_NAME,
                            exit_code=int(result.get("exit_code", 0) or 0),
                            duration_sec=round(float(result.get("duration_ms", 0) or 0) / 1000.0, 3),
                            timed_out=bool(result.get("timed_out", False)),
                            stdout_bytes=min(stdout_bytes, max_bytes),
                            stderr_bytes=min(stderr_bytes, max_bytes))


def blocked_run(reason: str, category: str) -> SandboxRunResult:
    """The record left behind when the guard Filter refuses a run before it reaches the sandbox."""
    return SandboxRunResult(script=_SCRIPT_NAME, blocked=True, block_reason=reason, block_category=category)
