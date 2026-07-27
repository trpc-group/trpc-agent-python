"""Expose the review execution policy as a tRPC-Agent tool filter.

The deterministic CLI calls :class:`ReviewExecutionFilter` directly before it
touches the sandbox. When the same skill is mounted on an ``LlmAgent`` the model
drives ``skill_run`` itself, so the policy has to sit on the framework's own
governance path instead. This module adapts it to ``BaseFilter``.

The deny contract mirrors ``ToolCallbackFilter._before`` in
``trpc_agent_sdk/agents/_callback.py``: setting ``rsp.rsp`` and clearing
``rsp.is_continue`` short-circuits the call, so the tool body never runs and the
model receives the refusal as the tool's response. ``rsp.error`` stays ``None``
because a policy decision is an answer, not a crash.

The SDK registry constructs one no-argument default instance. Application code
should construct a filter per tool set when it needs a task-scoped intercept
sink; this avoids mixing concurrent review events in process-global state.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter, FilterResult, register_tool_filter

from .filtering import ReviewExecutionFilter
from .models import FilterDecision, SandboxRequest
from .redaction import contains_secret

# Tool arguments that can carry an executable command, in the order we trust them.
_COMMAND_KEYS = ("command", "cmd", "script", "args", "argv", "code", "chars")
_PATH_KEYS = (
    "path",
    "paths",
    "file",
    "files",
    "input_files",
    "output_files",
    "inputs",
    "outputs",
    "cwd",
)
_SAFE_ENV_KEYS = {
    "PATH",
    "PYTHONPATH",
    "PYTHONIOENCODING",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
}

_DEFAULT_POLICY = ReviewExecutionFilter()


def evaluate_tool_args(
    args: Any,
    *,
    policy: ReviewExecutionFilter | None = None,
) -> FilterDecision:
    """Apply the sandbox policy to the raw arguments of a tool call."""
    policy = policy or _DEFAULT_POLICY
    if not isinstance(args, dict):
        return FilterDecision(
            action="allow", rule_id="allow", reason="no inspectable arguments"
        )

    command = _collect(args, _COMMAND_KEYS)
    paths = _collect(args, _PATH_KEYS)

    sensitive_field = _find_sensitive_field(args, command=command, paths=paths)
    if sensitive_field:
        return FilterDecision(
            action="deny",
            rule_id="sensitive.unredacted_input",
            reason=(
                f"unredacted sensitive data was detected in {sensitive_field}; "
                "redact it before sandbox execution"
            ),
        )

    env = args.get("env")
    if isinstance(env, Mapping):
        denied_env = sorted(
            str(key)
            for key in env
            if str(key) not in _SAFE_ENV_KEYS
            and not str(key).startswith("TRPC_REVIEW_")
        )
        if denied_env:
            return FilterDecision(
                action="deny",
                rule_id="env.not_whitelisted",
                reason=f"environment variables are not allowlisted: {', '.join(denied_env[:10])}",
                command=" ".join(command),
            )

    timeout_values = [
        _as_float(args[key], default=math.inf)
        for key in ("timeout_seconds", "timeout", "timeout_sec")
        if key in args
    ]
    # A tool may expose more than one timeout alias. Judge the largest request
    # so an ignored low-valued alias cannot hide the value the handler uses.
    timeout_seconds = max(timeout_values, default=10.0)
    if timeout_seconds <= 0:
        timeout_seconds = policy.max_timeout_seconds
    max_output_bytes = _as_int(
        args.get("max_output_bytes", policy.max_output_bytes),
        default=policy.max_output_bytes,
    )

    decision = policy.evaluate_request(
        SandboxRequest(
            name="llm-tool-call",
            command=command,
            display_command=" ".join(command),
            cwd=str(args.get("cwd") or "."),
            # Preserve the caller's requested budgets. Clamping here would turn
            # an over-budget request into an allowed one before the policy ever
            # had a chance to reject it.
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
    )
    if not decision.allowed:
        return decision

    # SkillRunTool's legacy ``output_files`` path calls ``fs.collect`` with
    # SDK-owned defaults, and omitting both fields triggers an implicit
    # ``out/**`` export.  Neither path gives the review policy a complete,
    # caller-visible byte envelope, so model-driven skill runs must use one
    # explicit declarative manifest.
    is_skill_run = bool(str(args.get("skill") or "").strip())
    if is_skill_run and _flatten_strings(args.get("output_files")):
        return FilterDecision(
            action="deny",
            rule_id="budget.legacy_outputs",
            reason=(
                "legacy output_files collection is disabled; use one explicit "
                "bounded outputs manifest"
            ),
            command=" ".join(command),
        )
    if is_skill_run and args.get("outputs") is None:
        return FilterDecision(
            action="deny",
            rule_id="budget.output_spec",
            reason=(
                "skill_run must declare explicit outputs with positive max_files, "
                "max_file_bytes, and max_total_bytes"
            ),
            command=" ".join(command),
        )
    if is_skill_run and _is_truthy(args.get("save_as_artifacts")):
        return FilterDecision(
            action="deny",
            rule_id="output.artifact_save",
            reason="saving raw workspace outputs as artifacts is disabled for code review",
            command=" ".join(command),
        )

    output_decision = _evaluate_output_spec(args.get("outputs"), policy=policy)
    if output_decision is not None:
        output_decision.command = " ".join(command)
        return output_decision

    for path in paths:
        path_decision = policy.evaluate_path(path)
        if not path_decision.allowed:
            path_decision.command = " ".join(command)
            return path_decision

    return FilterDecision(
        action="allow", rule_id="allow", reason="tool call passed review policy"
    )


def _find_sensitive_field(
    args: dict[str, Any],
    *,
    command: list[str],
    paths: list[str],
) -> str:
    """Return the first tool field carrying a credential-like value."""
    candidates = (
        ("command", command),
        ("stdin", _flatten_strings(args.get("stdin"))),
        ("editor_text", _flatten_strings(args.get("editor_text"))),
        ("path", paths),
    )
    for field, values in candidates:
        if any(contains_secret(value) for value in values):
            return field

    env = args.get("env")
    if isinstance(env, Mapping):
        for key, value in env.items():
            if contains_secret(str(key)):
                return "environment name"
            for item in _flatten_strings(value):
                if contains_secret(f"{key}={item}") or contains_secret(item):
                    return "environment value"
    return ""


def _evaluate_output_spec(
    value: Any,
    *,
    policy: ReviewExecutionFilter,
) -> FilterDecision | None:
    """Fail closed when declarative output limits exceed the review policy."""
    if value is None:
        return None
    output_spec = _as_mapping(value)
    if output_spec is None:
        return FilterDecision(
            action="deny",
            rule_id="budget.output_spec",
            reason="declarative outputs must be an inspectable object",
        )
    if _is_truthy(output_spec.get("save")):
        return FilterDecision(
            action="deny",
            rule_id="output.artifact_save",
            reason="saving raw workspace outputs as artifacts is disabled for code review",
        )

    limits: dict[str, int] = {}
    for key in ("max_files", "max_file_bytes", "max_total_bytes"):
        parsed = _strict_positive_int(output_spec.get(key))
        if parsed is None:
            return FilterDecision(
                action="deny",
                rule_id="budget.output_spec",
                reason=(
                    f"declarative outputs must set a positive explicit {key}; "
                    "zero or omission selects an SDK default outside the review policy"
                ),
            )
        limits[key] = parsed

    if limits["max_files"] > policy.max_output_files:
        return FilterDecision(
            action="deny",
            rule_id="budget.output_files",
            reason=(
                f"declarative output file count {limits['max_files']} exceeds budget "
                f"{policy.max_output_files}"
            ),
        )
    globs = _flatten_strings(output_spec.get("globs"))
    if not globs:
        return FilterDecision(
            action="deny",
            rule_id="budget.output_spec",
            reason="declarative outputs must include at least one explicit glob",
        )
    if len(globs) > policy.max_output_files:
        return FilterDecision(
            action="deny",
            rule_id="budget.output_files",
            reason=(
                f"declarative output glob count {len(globs)} exceeds budget "
                f"{policy.max_output_files}"
            ),
        )
    for key in ("max_file_bytes", "max_total_bytes"):
        if limits[key] > policy.max_output_bytes:
            return FilterDecision(
                action="deny",
                rule_id="budget.output",
                reason=(
                    f"declarative {key} {limits[key]} exceeds byte budget "
                    f"{policy.max_output_bytes}"
                ),
            )
    return None


@register_tool_filter("code_review_sandbox_policy")
class CodeReviewSandboxPolicyFilter(BaseFilter):
    """Block high-risk tool calls before the framework executes them."""

    def __init__(
        self,
        policy: ReviewExecutionFilter | None = None,
        intercept_sink: Callable[[FilterDecision], None] | None = None,
    ) -> None:
        self.policy = policy or ReviewExecutionFilter()
        self.intercept_sink = intercept_sink

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        decision = evaluate_tool_args(req, policy=self.policy)
        if decision.allowed:
            return

        if self.intercept_sink is not None:
            self.intercept_sink(decision)

        rsp.rsp = {
            "error": "denied_by_review_policy",
            "action": decision.action,
            "rule_id": decision.rule_id,
            "reason": decision.reason,
            "guidance": (
                "This call was stopped before execution. Record it as a manual-review item "
                "in the report instead of retrying it."
            ),
        }
        rsp.is_continue = False
        rsp.error = None


def _collect(args: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    """Flatten every string the given argument keys contribute."""
    out: list[str] = []
    for key in keys:
        out.extend(_flatten_strings(args.get(key)))
    return out


def _flatten_strings(value: Any) -> list[str]:
    """Return nested string values without mistaking mapping keys for paths."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        out: list[str] = []
        for item in value.values():
            out.extend(_flatten_strings(item))
        return out
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        out = []
        for item in value:
            out.extend(_flatten_strings(item))
        return out
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _flatten_strings(model_dump())
    return []


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    """Return a mapping view for raw dictionaries and Pydantic models."""
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, Mapping) else None
    return None


def _strict_positive_int(value: Any) -> int | None:
    """Parse the positive integers used by ``WorkspaceOutputSpec``."""
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    if isinstance(value, str) and str(parsed) != value.strip():
        return None
    return parsed if parsed > 0 else None


def _as_float(value: Any, *, default: float) -> float:
    """Parse a numeric tool argument without letting malformed input crash the filter."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _as_int(value: Any, *, default: int) -> int:
    """Parse an integer tool argument without letting malformed input crash the filter."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_truthy(value: Any) -> bool:
    """Recognize JSON/Pydantic-style true values without accepting arbitrary text."""
    if value is True:
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False
