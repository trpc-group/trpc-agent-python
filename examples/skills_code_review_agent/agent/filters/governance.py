# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Filter governance — pre-sandbox gate (Phase 3, L3).

Four check classes run BEFORE a script enters the sandbox. ``deny`` and
``needs_human_review`` never reach the sandbox — the orchestrator (P5)
records the block in ``filter_block`` and skips execution.

This module provides two layers:
* :class:`FilterGovernance` — the spec contract: a synchronous ``decide()``
  that inspects script content + budget and returns a :class:`FilterDecision`.
* :class:`CrGovernanceFilter` — an SDK :class:`BaseFilter` adapter that wraps
  ``FilterGovernance`` so the same checks run when the Skill is invoked via
  the SDK tool pipeline (``register_tool_filter``). The four check *strategies*
  are self-implemented (the SDK ships no built-in policy filters).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.filter import register_tool_filter


@dataclass
class FilterDecision:
    """Outcome of a governance check."""

    verdict: str  # allow|deny|needs_human_review
    reason: str  # high-risk|forbidden-path|network|budget|ok
    target: str  # the script path / target inspected
    detail: str


# --- high-risk script patterns (deny) ---
# Kept precise to avoid false positives on docs/comments: e.g. `rm -rf` only
# fires when followed by a path sigil (/, ~), not bare prose.
_HIGH_RISK_PATTERNS = [
    (re.compile(r"rm\s+-rf?\s+[/~]"), "rm -rf targeting a root/home path"),
    (re.compile(r"\bsudo\b\s+\w"), "sudo invocation"),
    (re.compile(r"\bcurl\b\s+https?://|\bwget\b\s+https?://"), "remote download+exec pattern (curl/wget)"),
    (re.compile(r"\beval\s*\("), "eval() arbitrary code execution"),
    (re.compile(r"os\.system\s*\(\s*['\"].*['\"]\s*\+"), "os.system with string concatenation (injection)"),
    (re.compile(r"subprocess\.(?:call|run|Popen)\s*\([^)]*shell\s*=\s*True[^)]*\+"), "shell=True with concatenation (injection)"),
]

# --- forbidden paths (deny) ---
_FORBIDDEN_PATH_PATTERNS = [
    re.compile(r"['\"]/etc/"),
    re.compile(r"['\"]~/.ssh"),
    re.compile(r"['\"]/?root/\."),
    re.compile(r"['\"]/?var/log"),
    re.compile(r"['\"]C:\\\\?Windows"),
    re.compile(r"['\"]/?proc/"),
]

# --- network access (needs_human_review) ---
_NETWORK_PATTERNS = [
    re.compile(r"\bsocket\.connect\s*\("),
    re.compile(r"\brequests\.(?:get|post|put|delete|patch|head)\s*\("),
    re.compile(r"\burllib\.request\.urlopen\s*\("),
    re.compile(r"\baiohttp\.ClientSession\s*\("),
    re.compile(r"\bhttpx\.(?:get|post|Client)\s*\("),
]
# Domains that are always allowed (won't trigger network review).
_NETWORK_WHITELIST = re.compile(r"localhost|127\.0\.0\.1|0\.0\.0\.0|::1")

# Budget thresholds (over → needs_human_review).
_BUDGET_DURATION_S = 60
_BUDGET_MEMORY_MB = 1024


class FilterGovernance:
    """Synchronous four-class governance check.

    Call :meth:`decide` with the script path + content + budget estimate.
    The orchestrator records ``deny``/``needs_human_review`` into
    ``filter_block`` and skips the sandbox for those verdicts.
    """

    def decide(
        self,
        script_path: str,
        script_content: str,
        budget: dict | None = None,
    ) -> FilterDecision:
        target = script_path
        budget = budget or {}

        # Strip comment lines (first non-space char is '#') to reduce false
        # positives — a doc/comment mentioning `rm -rf` should NOT trigger a
        # deny. Simple heuristic; P6 can refine with AST-aware comment skip.
        code = "\n".join(
            ln for ln in script_content.splitlines()
            if not ln.lstrip().startswith("#")
        )

        # 1. high-risk script features → deny
        for pat, desc in _HIGH_RISK_PATTERNS:
            m = pat.search(code)
            if m:
                return FilterDecision(
                    verdict="deny",
                    reason="high-risk",
                    target=target,
                    detail=f"{desc}: matched /{pat.pattern}/ → {m.group(0)!r}",
                )

        # 2. forbidden paths → deny
        for pat in _FORBIDDEN_PATH_PATTERNS:
            m = pat.search(code)
            if m:
                return FilterDecision(
                    verdict="deny",
                    reason="forbidden-path",
                    target=target,
                    detail=f"access to forbidden path: {m.group(0)!r}",
                )

        # 3. non-whitelisted network → needs_human_review
        for pat in _NETWORK_PATTERNS:
            m = pat.search(code)
            if m:
                # allow if the match context mentions a whitelisted host
                window = code[max(0, m.start() - 40): m.end() + 60]
                if _NETWORK_WHITELIST.search(window):
                    continue
                return FilterDecision(
                    verdict="needs_human_review",
                    reason="network",
                    target=target,
                    detail=f"non-whitelisted network access: {m.group(0)!r}",
                )

        # 4. over-budget → needs_human_review
        est_dur = float(budget.get("estimated_duration_s", 0) or 0)
        est_mem = float(budget.get("estimated_memory_mb", 0) or 0)
        if est_dur > _BUDGET_DURATION_S or est_mem > _BUDGET_MEMORY_MB:
            return FilterDecision(
                verdict="needs_human_review",
                reason="budget",
                target=target,
                detail=f"over budget: duration={est_dur}s (>{_BUDGET_DURATION_S}), memory={est_mem}MB (>{_BUDGET_MEMORY_MB})",
            )

        return FilterDecision(
            verdict="allow", reason="ok", target=target, detail="passed all governance checks"
        )


# --------------------------------------------------------------------------- #
# SDK BaseFilter adapter — same checks run when the Skill is invoked via the
# SDK tool pipeline. Registered as "cr_governance" tool filter.
# --------------------------------------------------------------------------- #
@register_tool_filter("cr_governance")
class CrGovernanceFilter(BaseFilter):
    """SDK tool filter wrapping :class:`FilterGovernance`.

    When a skill_run/skill_exec tool call carries a script to execute, this
    filter runs the four-class governance check in ``_before``. A ``deny``
    verdict sets ``is_continue=False`` so the call never reaches the sandbox;
    ``needs_human_review`` likewise blocks (the orchestrator records it).
    The block reason is surfaced via ``rsp.error``.
    """

    def __init__(self, governance: "FilterGovernance | None" = None):
        self._gov = governance or FilterGovernance()

    async def _before(self, ctx, req, rsp: FilterResult) -> None:
        # Extract script info from the tool-call request. The exact req shape
        # depends on the SDK tool pipeline; we try common accessors and fall
        # back to allow when no script is identifiable (P5 orchestrator wires
        # the real script_path into the request args).
        script_path = None
        script_content = None
        for attr in ("script_path", "path", "file"):
            script_path = getattr(req, attr, None)
            if script_path:
                break
        if script_path is None and isinstance(req, dict):
            script_path = req.get("script_path") or req.get("path")
        if script_path is None:
            return  # nothing to inspect → allow
        try:
            script_content = Path(script_path).read_text(encoding="utf-8")
        except Exception:
            script_content = ""
        decision = self._gov.decide(str(script_path), script_content, {})
        if decision.verdict in ("deny", "needs_human_review"):
            rsp.is_continue = False
            rsp.error = ValueError(
                f"[cr_governance] {decision.verdict}: {decision.reason} — {decision.detail}"
            )
