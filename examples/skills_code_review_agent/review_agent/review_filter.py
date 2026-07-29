# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Governance filters for the review agent's tool surface.

The SDK filter contract (``BaseFilter``) has no deny/needs_human_review
notion: a filter blocks a tool call by setting ``rsp.rsp`` to a substitute
result and ``rsp.is_continue = False`` in ``_before`` — the tool handler is
then never invoked and the model reads the substitute result.  We build the
review-domain decision enum on top of that mechanism.

``needs_human_review`` semantics in a batch CLI (nobody to ask mid-run): the
call is NOT executed, the event is persisted, and the item surfaces in the
report's human-review section.

Every decision — including ALLOW — is recorded to the ``filter_event`` table
by the filter itself (the SDK filter system has no built-in event log).

Defence layering (kept deliberately non-overlapping):
  1. ``SkillRunTool(allowed_cmds=["python3"])`` — what can execute at all
     (shell metacharacters, first token) — SDK built-in;
  2. this filter — review-domain policy: script allowlist, path escapes,
     env injection, input-source confinement, timeout clamp, execution
     budget, retry-loop cutoff; every decision persisted;
  3. agent-level ``after_tool_callback`` — output redaction (see redactor).
"""

from __future__ import annotations

import enum
import json
import shlex
from dataclasses import dataclass, field
from typing import Any, Optional

from trpc_agent_sdk.filter import BaseFilter

from .store import FilterEvent, ReviewStore, digest


class Decision(str, enum.Enum):
    """Review-domain governance decision."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


@dataclass
class FilterPolicy:
    """Static policy knobs for one review run."""

    #: exact relative script paths the agent may execute without questions
    allowed_scripts: tuple[str, ...] = ("scripts/run_checks.py", "scripts/parse_diff.py")
    #: only this skill may be loaded/run
    allowed_skill: str = "code-review"
    #: env keys the model may pass through to the sandbox
    allowed_env_keys: tuple[str, ...] = ("PYTHONPATH", "PYTHONHASHSEED", "LANG", "LC_ALL")
    #: host:// input sources must live under one of these absolute prefixes
    allowed_input_prefixes: tuple[str, ...] = ()
    #: hard ceiling applied to args["timeout"] (seconds)
    max_timeout_s: int = 60
    #: max sandbox executions per task
    max_runs: int = 4
    #: max cumulative sandbox wall time per task (seconds)
    max_total_sandbox_s: int = 120
    #: consecutive non-allow decisions before the terminal stop message
    max_denies_before_stop: int = 3


@dataclass
class FilterState:
    """Mutable per-task counters shared by all filter instances of a run."""

    runs_started: int = 0
    sandbox_seconds_budgeted: float = 0.0
    consecutive_denies: int = 0
    events: list[dict] = field(default_factory=list)


class FilterRecorder:
    """Persists every decision to the filter_event table + in-memory copy."""

    def __init__(self, store: Optional[ReviewStore], task_id: str, state: FilterState) -> None:
        self._store = store
        self._task_id = task_id
        self.state = state

    async def record(self, tool_name: str, decision: Decision, rule: str, reason: str, args: dict) -> None:
        try:
            args_digest = digest(json.dumps(args, ensure_ascii=False, default=str)[:800])
        except Exception:  # pylint: disable=broad-except
            args_digest = "<unserializable args>"
        event = {
            "tool_name": tool_name,
            "decision": decision.value,
            "rule": rule,
            "reason": reason,
            "args_digest": args_digest,
        }
        self.state.events.append(event)
        if self._store is not None:
            await self._store.add(FilterEvent(task_id=self._task_id, **event))


def _substitute_result(decision: Decision, rule: str, reason: str, suggestion: str) -> dict:
    """The dict the model receives instead of a tool result when blocked."""
    return {
        "status": "denied" if decision is Decision.DENY else "needs_human_review",
        "rule": rule,
        "reason": reason,
        "suggestion": suggestion,
    }


class ReviewToolFilter(BaseFilter):
    """Policy filter attached to one tool instance (skill_load or skill_run)."""

    def __init__(self, tool_name: str, policy: FilterPolicy, recorder: FilterRecorder) -> None:
        super().__init__()
        self.name = f"review_filter_{tool_name}"
        self._tool_name = tool_name
        self._policy = policy
        self._recorder = recorder

    # -- decision logic ----------------------------------------------------

    def _decide_skill_load(self, args: dict) -> tuple[Decision, str, str, str]:
        skill = str(args.get("skill_name", "")).strip()
        if skill != self._policy.allowed_skill:
            return (Decision.DENY, "skill_allowlist", f"skill {skill!r} is not allowed in a review run",
                    f"only {self._policy.allowed_skill!r} may be loaded")
        return Decision.ALLOW, "skill_allowlist", "", ""

    def _decide_skill_run(self, args: dict) -> tuple[Decision, str, str, str]:
        policy = self._policy

        skill = str(args.get("skill", "")).strip()
        if skill != policy.allowed_skill:
            return (Decision.DENY, "skill_allowlist", f"skill {skill!r} is not allowed",
                    f"run checks via the {policy.allowed_skill!r} skill")

        command = str(args.get("command", ""))
        try:
            tokens = shlex.split(command)
        except ValueError as ex:
            return Decision.DENY, "command_parse", f"unparsable command: {ex}", "use: python3 scripts/run_checks.py"
        if not tokens:
            return Decision.DENY, "command_parse", "empty command", "use: python3 scripts/run_checks.py"
        if tokens[0] not in ("python3", "python"):
            return (Decision.DENY, "command_allowlist", f"command {tokens[0]!r} is not python3",
                    "only python3 rule scripts are executable in a review run")

        script = tokens[1] if len(tokens) > 1 else ""
        norm = script.replace("\\", "/").lstrip("./")
        if script.startswith("/") or ".." in norm.split("/"):
            return (Decision.DENY, "script_path", f"path escape in script path {script!r}",
                    "reference scripts relative to the skill root, e.g. scripts/run_checks.py")
        if norm not in policy.allowed_scripts:
            if norm.startswith("scripts/") and norm.endswith(".py"):
                return (Decision.NEEDS_HUMAN_REVIEW, "script_allowlist",
                        f"script {script!r} is not on the reviewed allowlist",
                        "a human must vet new scripts before they run in the sandbox")
            return (Decision.DENY, "script_allowlist", f"{script!r} is not a known skill script",
                    f"allowed: {', '.join(policy.allowed_scripts)}")

        env = args.get("env") or {}
        bad_keys = [key for key in env if key not in policy.allowed_env_keys]
        if bad_keys:
            return (Decision.DENY, "env_allowlist", f"env keys not allowed: {', '.join(sorted(bad_keys)[:5])}",
                    f"allowed env keys: {', '.join(policy.allowed_env_keys)}")

        for spec in args.get("inputs") or []:
            src = str(spec.get("src", "") if isinstance(spec, dict) else getattr(spec, "src", ""))
            if src.startswith("host://"):
                host_path = src[len("host://"):]
                if not any(host_path.startswith(prefix) for prefix in policy.allowed_input_prefixes):
                    return (Decision.DENY, "input_confinement",
                            f"host input {host_path!r} is outside the task input directory",
                            "only the staged review input may be mounted")
            elif src.startswith(("workspace://", "skill://", "artifact://")):
                continue
            elif src:
                return Decision.DENY, "input_confinement", f"unknown input scheme: {src!r}", ""

        state = self._recorder.state
        if state.runs_started >= policy.max_runs:
            return (Decision.DENY, "budget_runs", f"execution budget exhausted ({policy.max_runs} runs)",
                    "summarize with the results you already have")
        requested = float(args.get("timeout") or 0) or policy.max_timeout_s
        effective = min(requested, policy.max_timeout_s)
        if state.sandbox_seconds_budgeted + effective > policy.max_total_sandbox_s:
            return (Decision.DENY, "budget_time",
                    f"cumulative sandbox time budget exceeded ({policy.max_total_sandbox_s}s)",
                    "summarize with the results you already have")
        return Decision.ALLOW, "", "", ""

    # -- BaseFilter hook ---------------------------------------------------

    async def _before(self, ctx: Any, req: Any, rsp) -> None:
        args = req if isinstance(req, dict) else {}
        policy = self._policy
        state = self._recorder.state

        # retry-loop cutoff: after N consecutive blocks return a terminal
        # instruction instead of yet another denial the model may retry around
        if state.consecutive_denies >= policy.max_denies_before_stop:
            await self._recorder.record(self._tool_name, Decision.DENY, "retry_cutoff",
                                        "too many blocked attempts, terminating tool phase", args)
            rsp.rsp = {
                "status": "denied",
                "rule": "retry_cutoff",
                "reason": "execution budget exhausted after repeated blocked attempts",
                "suggestion": "STOP calling tools. Produce the final review summary from the data you have.",
            }
            rsp.is_continue = False
            return

        if self._tool_name == "skill_load":
            decision, rule, reason, suggestion = self._decide_skill_load(args)
        else:
            decision, rule, reason, suggestion = self._decide_skill_run(args)

        if decision is Decision.ALLOW:
            state.consecutive_denies = 0
            if self._tool_name == "skill_run":
                # clamp the timeout the model asked for; record when changed
                requested = float(args.get("timeout") or 0)
                effective = min(requested or policy.max_timeout_s, policy.max_timeout_s)
                if requested != effective:
                    await self._recorder.record(self._tool_name, Decision.ALLOW, "timeout_clamp",
                                                f"timeout clamped {requested or 'default'} -> {effective}s", args)
                args["timeout"] = int(effective)
                state.runs_started += 1
                state.sandbox_seconds_budgeted += effective
            await self._recorder.record(self._tool_name, Decision.ALLOW, rule or "policy", "ok", args)
            return

        state.consecutive_denies += 1
        await self._recorder.record(self._tool_name, decision, rule, reason, args)
        rsp.rsp = _substitute_result(decision, rule, reason, suggestion)
        rsp.is_continue = False
