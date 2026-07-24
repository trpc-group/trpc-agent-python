"""Pre-execution safety filter.

This adapter demonstrates the seam where the guard plugs into a Tool /
Skill execution pipeline. Its ``_before``/``_after`` hooks mirror the
framework filter shape, but it is deliberately standalone and is not a
drop-in ``BaseFilter`` under the current SDK ordering rules. Use the wrapper
for present-day enforcement; a future terminal phase can invoke this adapter
after callback mutations have completed.

For environments where direct framework wiring is not yet available, the
:class:`ToolScriptSafetyFilter` also exposes a synchronous
``check(tool_name, args)`` API that the standalone wrapper consumes.
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime as _dt
import logging
from typing import Any, Coroutine, Mapping, TypeVar

from trpc_agent_sdk.tools.safety._audit import AuditSink, InMemoryAuditSink, NullAuditSink
from trpc_agent_sdk.tools.safety._exceptions import (
    SafetyAuditError,
    ToolRequestError,
)
from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._models import (
    SafetyDecision,
    SafetyReport,
    SafetyScanRequest,
    ToolKind,
)
from trpc_agent_sdk.tools.safety._policy import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety._telemetry import TelemetrySink, build_audit_event, get_default_sink
from trpc_agent_sdk.tools.safety._tool_adapter import (
    ToolInputAdapter,
    build_default_adapters,
    resolve_adapter,
)

# ContextVar so concurrent tool calls do not share trace state. The value
# is the sanitized arguments to emit on the next ``trace_tool_call``.
_trace_args_var: contextvars.ContextVar[tuple[str, ...] | None] = \
    contextvars.ContextVar("tool_safety_trace_args", default=None)
_LOGGER = logging.getLogger(__name__)
_ResultT = TypeVar("_ResultT")


class BlockedExecutionError(Exception):
    """Raised by ``enforce`` when execution must not proceed.

    The ``report`` attribute gives callers all the context they need to
    render a structured error response without re-scanning.
    """

    def __init__(self, report: SafetyReport, message: str = "") -> None:
        super().__init__(message or report.recommendation)
        self.report = report


class ToolScriptSafetyFilter:
    """Terminal pre-execution safety filter.

    Usage (sync API, used by the wrapper and tests)::

        policy = load_safety_policy("policy.yaml")
        guard = ToolSafetyGuard(policy)
        flt = ToolScriptSafetyFilter(guard, audit_sink=JsonlAuditSink(...))
        decision, report = flt.check("workspace_exec", {"command": "ls"})

    The ``_before``/``_after`` hooks are reserved for a future SDK terminal
    phase. Do not add this object to today's ``filters=`` list: ordinary
    callbacks can still mutate arguments after the configured filters run.

    The filter follows the plan's ``fail-closed`` posture: ``deny`` and
    un-approved ``needs_human_review`` block execution; audit failures
    block execution when ``policy.audit.required`` is true.
    """

    # Metadata for the future terminal-phase seam. It has no effect until the
    # framework explicitly implements terminal ordering.
    terminal_before_handler: bool = True

    def __init__(
        self,
        guard: ToolSafetyGuard,
        *,
        audit_sink: AuditSink | None = None,
        telemetry: TelemetrySink | None = None,
        builtin_adapters: dict[str, ToolInputAdapter] | None = None,
    ) -> None:
        self.guard = guard
        self.policy: ToolSafetyPolicy = guard.policy
        self.audit_sink: AuditSink = audit_sink or (NullAuditSink()
                                                    if not self.policy.audit.enabled else InMemoryAuditSink())
        self._telemetry = telemetry
        self._builtin = builtin_adapters or build_default_adapters(self.policy)

    # ------------------------------------------------------------------ #
    # Decision-and-recording interface
    # ------------------------------------------------------------------ #

    def check(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        *,
        tool_kind: ToolKind = ToolKind.UNKNOWN,
        metadata: Mapping[str, Any] | None = None,
    ) -> tuple[SafetyDecision, SafetyReport]:
        """Scan inputs and return (decision, report).

        Performs audit + telemetry but does *not* raise on deny/review.
        Callers that want fail-closed behavior should use ``enforce``.
        """

        request = self._build_request(tool_name, args, tool_kind=tool_kind, metadata=metadata)
        return self._run_sync(self.check_request_async(request))

    async def check_async(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        *,
        tool_kind: ToolKind = ToolKind.UNKNOWN,
        metadata: Mapping[str, Any] | None = None,
    ) -> tuple[SafetyDecision, SafetyReport]:
        """Async form of :meth:`check` that waits for required audit I/O."""
        request = self._build_request(tool_name, args, tool_kind=tool_kind, metadata=metadata)
        return await self.check_request_async(request)

    def check_request(
        self,
        request: SafetyScanRequest,
    ) -> tuple[SafetyDecision, SafetyReport]:
        """Scan and record an already-normalized request synchronously."""
        return self._run_sync(self.check_request_async(request))

    async def check_request_async(
        self,
        request: SafetyScanRequest,
    ) -> tuple[SafetyDecision, SafetyReport]:
        """Scan and durably record an already-normalized request."""
        report = self.guard.scan(request)
        blocked = self.blocks_execution(report)
        await self.record_report_async(request, report, blocked=blocked)
        return report.decision, report

    def enforce(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        *,
        tool_kind: ToolKind = ToolKind.UNKNOWN,
        metadata: Mapping[str, Any] | None = None,
    ) -> SafetyReport:
        """Like :meth:`check` but raises :class:`BlockedExecutionError`.

        Use this in the wrapper and any future framework hook so the
        caller's code path is the same regardless of how the block is
        reached.
        """

        request = self._build_request(tool_name, args, tool_kind=tool_kind, metadata=metadata)
        return self.enforce_request(request)

    async def enforce_async(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        *,
        tool_kind: ToolKind = ToolKind.UNKNOWN,
        metadata: Mapping[str, Any] | None = None,
    ) -> SafetyReport:
        """Async form of :meth:`enforce` for async delegates."""
        request = self._build_request(tool_name, args, tool_kind=tool_kind, metadata=metadata)
        return await self.enforce_request_async(request)

    def enforce_request(self, request: SafetyScanRequest) -> SafetyReport:
        """Fail closed unless the normalized request is allowed and audited."""
        return self._run_sync(self.enforce_request_async(request))

    async def enforce_request_async(
        self,
        request: SafetyScanRequest,
    ) -> SafetyReport:
        """Async fail-closed form of :meth:`enforce_request`."""
        decision, report = await self.check_request_async(request)
        if decision == SafetyDecision.ALLOW:
            return report
        if decision == SafetyDecision.NEEDS_HUMAN_REVIEW \
                and not self.policy.defaults.human_review_blocks_execution:
            return report
        raise BlockedExecutionError(report)

    # ------------------------------------------------------------------ #
    # Async API (duck-typed for future BaseFilter integration)
    # ------------------------------------------------------------------ #

    async def _before(self, ctx: Any, req: Any, rsp: Any) -> None:
        """Duck-typed hook for ``trpc_agent_sdk.filter.BaseFilter``.

        ``ctx`` is expected to expose ``tool_name`` (or be a string);
        ``req`` is expected to be a mapping of tool arguments or an
        object with ``arguments``. ``rsp`` is the framework's
        ``FilterResult``: we set ``is_continue`` and ``rsp`` on it.
        """

        tool_name = _resolve_tool_name(ctx, req)
        args = _resolve_args(req)
        tool_kind = _resolve_tool_kind(ctx, req)
        try:
            _, report = await self.check_async(tool_name, args, tool_kind=tool_kind)
        except ToolRequestError as exc:
            request = SafetyScanRequest(
                tool_name=tool_name,
                tool_kind=tool_kind,
                script="",
            )
            report = self.guard.error_report(request, exc)
            await self.record_report_async(request, report, blocked=True)
            _set_filter_continue(rsp, False)
            _set_filter_rsp(rsp, _render_block(report))
            return
        if report.decision == SafetyDecision.ALLOW:
            _set_filter_continue(rsp, True)
            self._set_trace_args(tool_name, args, report)
            return
        if report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW \
                and not self.policy.defaults.human_review_blocks_execution:
            _set_filter_continue(rsp, True)
            self._set_trace_args(tool_name, args, report)
            return
        _set_filter_continue(rsp, False)
        _set_filter_rsp(rsp, _render_block(report))

    async def _after(self, ctx: Any, req: Any, rsp: Any) -> None:
        # No post-execution work for now; the audit event is written in
        # ``check`` so it lands before the handler runs.
        return None

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def record_report_async(
        self,
        request: SafetyScanRequest,
        report: SafetyReport,
        *,
        blocked: bool,
    ) -> None:
        """Record telemetry and await the audit event before execution."""

        # Telemetry is best effort; audit is the enforcement-critical side
        # effect and therefore is awaited below.
        sink = self._telemetry or get_default_sink()
        try:
            sink.record(report, tool_name=request.tool_name, blocked=blocked)
        except (AttributeError, RuntimeError, TypeError) as exc:
            _LOGGER.warning(
                "tool safety telemetry recording failed: %s",
                type(exc).__name__,
            )
        event = build_audit_event(
            report=report,
            tool_name=request.tool_name,
            tool_kind=request.tool_kind,
            execution_blocked=blocked,
            timestamp=_utc_now_iso(),
        )
        try:
            await self.audit_sink.emit(event)
        except SafetyAuditError:
            if self.policy.audit.required:
                # Re-raise so the wrapper's fail-closed path engages.
                raise
        except Exception:  # pragma: no cover - defensive
            if self.policy.audit.required:
                raise SafetyAuditError("unexpected audit emit failure")

    def _build_request(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        *,
        tool_kind: ToolKind,
        metadata: Mapping[str, Any] | None,
    ) -> SafetyScanRequest:
        adapter = resolve_adapter(tool_name, self.policy, builtin=self._builtin)
        request = adapter.build_request(
            args,
            metadata=metadata,
        ) if _looks_like_args_dict(args) else _build_request_from_raw(
            tool_name,
            tool_kind,
            args,
            adapter,
        )
        if request.tool_kind == ToolKind.UNKNOWN:
            return request.model_copy(update={"tool_kind": tool_kind})
        return request

    def _run_sync(
        self,
        coroutine: Coroutine[Any, Any, _ResultT],
    ) -> _ResultT:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        coroutine.close()
        raise SafetyAuditError("synchronous safety enforcement cannot run inside an event loop; "
                               "use the async interface so required audit I/O is awaited")

    def blocks_execution(self, report: SafetyReport) -> bool:
        """Return whether policy requires this report to block execution."""

        return report.decision == SafetyDecision.DENY or (report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
                                                          and self.policy.defaults.human_review_blocks_execution)

    def _set_trace_args(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        report: SafetyReport,
    ) -> None:
        # Replace env/script with placeholders so downstream tracing
        # doesn't echo raw secrets.
        sanitized = dict(args)
        for key in ("env", "environment"):
            if key in sanitized and isinstance(sanitized[key], Mapping):
                sanitized[key] = {k: "<redacted>" for k in sanitized[key]}
        for key in ("script", "code", "command"):
            if key in sanitized:
                sanitized[key] = f"<redacted sha={report.script_sha256[:8]}>"
        _trace_args_var.set(tuple((f"{tool_name}", repr(sanitized))))


def _looks_like_args_dict(args: Any) -> bool:
    return isinstance(args, Mapping)


def _build_request_from_raw(
    tool_name: str,
    tool_kind: ToolKind,
    args: Any,
    adapter: ToolInputAdapter,
) -> SafetyScanRequest:
    """Fallback for when args is not a Mapping (e.g. raw string)."""

    if isinstance(args, str):
        return SafetyScanRequest(
            tool_name=tool_name,
            tool_kind=tool_kind,
            language=adapter.mapping.language,
            script=args,
        )
    if isinstance(args, Mapping):
        return adapter.build_request(args)
    raise ToolRequestError(f"unsupported args type {type(args)!r} for tool {tool_name!r}")


def _resolve_tool_name(ctx: Any, req: Any) -> str:
    for source in (req, ctx):
        for attr in ("tool_name", "name", "tool"):
            value = getattr(source, attr, None)
            if isinstance(value, str) and value:
                return value
    if isinstance(ctx, str):
        return ctx
    return "unknown"


def _resolve_args(req: Any) -> Mapping[str, Any]:
    if isinstance(req, Mapping):
        return req
    args = getattr(req, "arguments", None)
    if isinstance(args, Mapping):
        return args
    if isinstance(req, str):
        return {"command": req}
    return {}


def _resolve_tool_kind(ctx: Any, req: Any) -> ToolKind:
    for source in (req, ctx):
        value = getattr(source, "tool_kind", None)
        if isinstance(value, ToolKind):
            return value
        if isinstance(value, str):
            try:
                return ToolKind(value)
            except ValueError:
                continue
    return ToolKind.UNKNOWN


def _set_filter_continue(rsp: Any, value: bool) -> None:
    if rsp is None:
        return
    if hasattr(rsp, "is_continue"):
        try:
            rsp.is_continue = value
            return
        except Exception:  # pragma: no cover
            pass
    if isinstance(rsp, dict):
        rsp["is_continue"] = value


def _set_filter_rsp(rsp: Any, payload: Mapping[str, Any]) -> None:
    if rsp is None:
        return
    if hasattr(rsp, "rsp"):
        try:
            rsp.rsp = dict(payload)
            return
        except Exception:  # pragma: no cover
            pass
    if isinstance(rsp, dict):
        rsp.update(payload)


def _render_block(report: SafetyReport) -> dict[str, Any]:
    return {
        "tool_safety": {
            "report_id":
            report.report_id,
            "decision":
            report.decision.value,
            "risk_level":
            report.risk_level.label(),
            "rule_ids":
            list(report.rule_ids),
            "recommendation":
            report.recommendation,
            "policy_hash":
            report.policy_hash,
            "findings": [{
                "rule_id": f.rule_id,
                "category": f.category.value,
                "risk_level": f.risk_level.label(),
                "evidence": f.evidence.snippet,
                "location": {
                    "line": f.evidence.line,
                    "column": f.evidence.column,
                },
                "extras": dict(f.evidence.extras),
                "recommendation": f.recommendation,
            } for f in report.findings],
        },
    }


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()
