"""Native SDK filters for review telemetry and pre-execution policy."""
from __future__ import annotations

from typing import Any
import json
import re
import time

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter, FilterResult, register_agent_filter, register_tool_filter

from .policy import FilterDecision, evaluate_command
from .redactor import redact

_HOST_PATH = re.compile(r"host://[^\s\"']+")

def _safe(value: Any) -> Any:
    """Serialize SDK values for audit without storing credentials."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, str):
        return _HOST_PATH.sub("[HOST_PATH]", redact(value))
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return value


@register_agent_filter("code_review_agent_filter")
class CodeReviewAgentFilter(BaseFilter):
    """Attach lightweight lifecycle audit data to the Agent context."""

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        ctx.metadata["code_review_started"] = True
        ctx.metadata.setdefault("code_review_started_at", time.monotonic())


@register_tool_filter("code_review_skill_run_filter")
class CodeReviewSkillRunFilter(BaseFilter):
    """Block risky skill commands before the SDK workspace is invoked."""

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        args = getattr(req, "args", req if isinstance(req, dict) else {})
        command = args.get("command", "") if isinstance(args, dict) else ""
        timeout = 30
        decision: FilterDecision | None = None
        if isinstance(args, dict):
            try:
                timeout = int(args.get("timeout", 30))
            except (TypeError, ValueError):
                decision = FilterDecision("needs_human_review", "timeout must be a whole number of seconds")
        if isinstance(args, dict) and not args.get("inputs"):
            args["inputs"] = ctx.metadata.get("code_review_workspace_inputs", [])
        if isinstance(args, dict) and str(args.get("stdin", "")).strip():
            decision = FilterDecision(
                "needs_human_review",
                "stdin is not permitted; use the staged diff file path instead",
            )
        elif decision is None:
            decision = evaluate_command(command, timeout)
        if decision.decision == "allow" and command.lstrip().startswith("ruff "):
            staged = {item.get("dst", "") for item in args.get("inputs", []) if isinstance(item, dict)}
            if not any(path != "work/inputs/input.diff" for path in staged):
                decision = type(decision)("needs_human_review", "ruff requires a staged source file, not only a diff")
        decisions = ctx.metadata.setdefault("code_review_filter_decisions", [])
        decisions.append({
            "decision": decision.decision, "reason": decision.reason,
        })
        if decision.decision != "allow":
            ctx.metadata.setdefault("code_review_skill_runs", []).append({
                "runtime": "skill_run", "command": command, "status": "blocked", "exit_code": None,
                "stdout": "", "stderr": decision.reason, "timed_out": False, "duration_seconds": 0,
                "output_files": [],
            })
            rsp.error = PermissionError(f"{decision.decision}: {decision.reason}")
            rsp.is_continue = False

    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        args = getattr(req, "args", req if isinstance(req, dict) else {})
        command = args.get("command", "") if isinstance(args, dict) else ""
        output = _safe(rsp.rsp) if rsp.rsp is not None else {}
        if not isinstance(output, dict):
            output = {"stdout": str(output)}
        exit_code = output.get("exit_code")
        timed_out = bool(output.get("timed_out", False))
        ctx.metadata.setdefault("code_review_skill_runs", []).append({
            "runtime": "skill_run", "command": command,
            "status": "timed_out" if timed_out else "passed" if exit_code == 0 else "failed",
            "stdout": output.get("stdout", ""), "stderr": output.get("stderr", ""),
            "exit_code": exit_code, "timed_out": timed_out,
            "duration_seconds": output.get("duration_ms", 0) / 1000,
            "output_files": output.get("output_files", []),
        })


def before_model_audit(invocation_context: Any, request: Any) -> None:
    """Record the redacted model input and start time in InvocationContext state."""
    state = invocation_context.agent_context.metadata
    # The Agent receives trusted host-to-sandbox mappings out of band. The
    # user/model message deliberately contains only sandbox-relative paths.
    if not state.get("code_review_workspace_inputs"):
        trusted_inputs = getattr(invocation_context.agent, "_code_review_workspace_inputs", [])
        if trusted_inputs:
            state["code_review_workspace_inputs"] = trusted_inputs
        for content in getattr(request, "contents", []):
            for part in getattr(content, "parts", []):
                try:
                    payload = json.loads(getattr(part, "text", ""))
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict):
                    state["code_review_changed_lines"] = payload.get("changed_lines", [])
                    break
            if state.get("code_review_changed_lines"):
                break
    trusted_task_id = getattr(invocation_context.agent, "_code_review_task_id", "")
    trusted_output_dir = getattr(invocation_context.agent, "_code_review_output_dir", "")
    if trusted_task_id:
        state["code_review_task_id"] = trusted_task_id
    if trusted_output_dir:
        state["code_review_output_dir"] = trusted_output_dir
    state["code_review_model_pending"] = {
        "model": getattr(invocation_context.agent.model, "_model_name", type(invocation_context.agent.model).__name__),
        "input": _safe(request), "started": time.monotonic(),
    }


def after_model_audit(invocation_context: Any, response: Any) -> None:
    """Record each model result, including provider-declared failures."""
    pending = invocation_context.agent_context.metadata.pop("code_review_model_pending", {})
    invocation_context.agent_context.metadata.setdefault("code_review_model_runs", []).append({
        "model": pending.get("model", "unknown"), "input": pending.get("input", {}), "output": _safe(response),
        "duration_seconds": time.monotonic() - pending.get("started", time.monotonic()),
        "exception": getattr(response, "error_message", "") or "",
    })
