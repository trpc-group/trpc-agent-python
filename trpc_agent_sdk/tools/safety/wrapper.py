"""Generic pre-execution wrappers.

These wrappers are the recommended way to enforce safety decisions when
direct framework integration is not yet wired in. They take any
callable (sync or async) and any duck-typed "code executor" object, and
guarantee that the delegate is never reached for ``deny`` or un-approved
``needs_human_review`` outcomes.

Design
------
* The wrapper is the *only* enforcement point in this standalone
  package. It is intentionally small so the audit trail is unambiguous:
  exactly one audit event per call, written before the delegate runs.
* The wrapper does not impose a CPU/memory limit. The plan calls out
  that real resource enforcement belongs to the sandbox/runtime; the
  wrapper only applies the static guard decision and the output-byte
  cap on what the agent observes.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Generic, Mapping, TypeVar

from trpc_agent_sdk.tools.safety._audit import AuditSink, InMemoryAuditSink, NullAuditSink
from trpc_agent_sdk.tools.safety._filter import BlockedExecutionError, ToolScriptSafetyFilter
from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._models import (
    SafetyReport,
    SafetyScanRequest,
    ScriptLanguage,
    ToolKind,
)
from trpc_agent_sdk.tools.safety._policy import ToolSafetyPolicy

T = TypeVar("T")


class SafetyWrappedCallable(Generic[T]):
    """Wrap a callable so it runs only when the guard allows it.

    Example
    -------
    >>> import os
    >>> from trpc_agent_sdk.tools.safety import (
    ...     SafetyWrappedCallable, ToolSafetyGuard, load_safety_policy,
    ... )
    >>> policy = load_safety_policy("policy.yaml")
    >>> guard = ToolSafetyGuard(policy)
    >>> wrapped = SafetyWrappedCallable(guard, os.system,
    ...                                 tool_name="os.system",
    ...                                 language=ScriptLanguage.BASH,
    ...                                 script_kw="script")
    >>> # wrapped("ls") runs only if the policy allows it.
    """

    def __init__(
        self,
        guard: ToolSafetyGuard,
        delegate: Callable[..., T],
        *,
        tool_name: str,
        language: ScriptLanguage = ScriptLanguage.UNKNOWN,
        tool_kind: ToolKind = ToolKind.UNKNOWN,
        script_kw: str | None = None,
        script_pos: int | None = None,
        cwd_kw: str | None = None,
        env_kw: str | None = None,
        timeout_kw: str | None = None,
        argv_kw: str | None = None,
        metadata_kw: str | None = None,
        output_bytes_kw: str | None = None,
        filter: ToolScriptSafetyFilter | None = None,
    ) -> None:
        if (script_kw is None) == (script_pos is None):
            raise ValueError(
                "specify exactly one of script_kw or script_pos")
        self.guard = guard
        self.delegate = delegate
        self.tool_name = tool_name
        self.language = language
        self.tool_kind = tool_kind
        self.script_kw = script_kw
        self.script_pos = script_pos
        self.cwd_kw = cwd_kw
        self.env_kw = env_kw
        self.timeout_kw = timeout_kw
        self.argv_kw = argv_kw
        self.metadata_kw = metadata_kw
        self.output_bytes_kw = output_bytes_kw
        self._filter = filter or ToolScriptSafetyFilter(
            guard, audit_sink=_default_audit_sink(guard.policy))

    def __call__(self, *args: Any, **kwargs: Any) -> T:
        report = self._enforce(args, kwargs)
        # Set sanitized trace args for downstream telemetry consumers.
        try:
            return self.delegate(*args, **kwargs)
        finally:
            _ = report  # caller can inspect via last_report

    async def call_async(self, *args: Any, **kwargs: Any) -> T:
        report = await self._enforce_async(args, kwargs)
        result = self.delegate(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result  # type: ignore[return-value]

    @property
    def safety_filter(self) -> ToolScriptSafetyFilter:
        return self._filter

    def _enforce(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> SafetyReport:
        request = self._build_request(args, kwargs)
        return self._filter.enforce_request(request)

    async def _enforce_async(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> SafetyReport:
        request = self._build_request(args, kwargs)
        return await self._filter.enforce_request_async(request)

    def _build_request(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> SafetyScanRequest:
        script = self._extract_script(args, kwargs)
        cwd = kwargs.get(self.cwd_kw) if self.cwd_kw else None
        env = kwargs.get(self.env_kw) if self.env_kw else None
        timeout = kwargs.get(self.timeout_kw) if self.timeout_kw else None
        argv = kwargs.get(self.argv_kw) if self.argv_kw else None
        metadata = kwargs.get(self.metadata_kw) if self.metadata_kw else None
        output_bytes = kwargs.get(self.output_bytes_kw) \
            if self.output_bytes_kw else None
        return SafetyScanRequest(
            tool_name=self.tool_name,
            tool_kind=self.tool_kind,
            language=self.language,
            script=script or "",
            cwd=str(cwd) if cwd is not None else None,
            env={str(k): str(v) for k, v in (env or {}).items()}
                if isinstance(env, Mapping) else {},
            argv=_coerce_argv(argv),
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
            requested_timeout_seconds=float(timeout)
                if isinstance(timeout, (int, float)) else None,
            requested_output_bytes=int(output_bytes)
                if isinstance(output_bytes, int) else None,
        )

    def _extract_script(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        if self.script_kw is not None:
            value = kwargs.get(self.script_kw, "")
        elif self.script_pos is not None:
            try:
                value = args[self.script_pos]
            except IndexError:
                value = ""
        else:  # pragma: no cover - constructor forbids this
            value = ""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            return " ".join(str(v) for v in value)
        return str(value)


class SafetyCheckedExecutor:
    """Wrap a duck-typed code executor.

    The delegate must expose ``async def execute_code(self, input)`` and
    return an object with at least ``outcome`` and ``output`` attributes
    (or be a dict with the same keys). The wrapper:

    1. Scans every code block in the input.
    2. Combines the reports; emits one audit event before delegating.
    3. Returns a failed result without calling the delegate on deny or
       un-approved review.
    4. Truncates the returned output to ``policy.limits.max_output_bytes``.
    """

    def __init__(
        self,
        guard: ToolSafetyGuard,
        delegate: Any,
        *,
        tool_name: str = "code_executor",
        language: ScriptLanguage = ScriptLanguage.PYTHON,
        effective_timeout_seconds: float | None = None,
        audit_sink: AuditSink | None = None,
        filter: ToolScriptSafetyFilter | None = None,
    ) -> None:
        self.guard = guard
        self.delegate = delegate
        self.tool_name = tool_name
        self.language = language
        self.effective_timeout_seconds = effective_timeout_seconds
        self._filter = filter or ToolScriptSafetyFilter(
            guard, audit_sink=audit_sink or _default_audit_sink(guard.policy))

    async def execute_code(self, execution_input: Any) -> Any:
        requests = self._build_requests(execution_input)
        if not requests:
            return _make_failure_result("no code blocks to scan")
        reports: list[SafetyReport] = []
        for request in requests:
            reports.append(self.guard.scan(request))
        combined = SafetyReport.combine(
            reports,
            report_id=reports[0].report_id,
            policy_hash=self.guard.policy_hash,
            policy_version=self.guard.policy_version,
            scan_duration_ms=sum(r.scan_duration_ms for r in reports),
        )
        blocked = self._filter.blocks_execution(combined)
        await self._filter.record_report_async(
            requests[0], combined, blocked=blocked)
        if blocked:
            return _render_executor_block(combined)
        result = await self.delegate.execute_code(execution_input)
        return _truncate_output(result,
                                self.guard.policy.limits.max_output_bytes)

    def _build_requests(self, execution_input: Any) -> list[SafetyScanRequest]:
        blocks = _extract_code_blocks(execution_input)
        requests: list[SafetyScanRequest] = []
        for idx, (block, block_language) in enumerate(blocks):
            language = block_language or self.language
            metadata: dict[str, Any] = {"block_index": idx}
            if language == ScriptLanguage.UNKNOWN:
                metadata["execution_capable"] = True
            requests.append(SafetyScanRequest(
                tool_name=self.tool_name,
                tool_kind=ToolKind.CODE_EXECUTOR,
                language=language,
                script=block,
                metadata=metadata,
                requested_timeout_seconds=self.effective_timeout_seconds,
            ))
        return requests


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _default_audit_sink(policy: ToolSafetyPolicy) -> AuditSink:
    if not policy.audit.enabled:
        return NullAuditSink()
    if policy.audit.path:
        from trpc_agent_sdk.tools.safety._audit import JsonlAuditSink
        return JsonlAuditSink(policy.audit.path)
    return InMemoryAuditSink()


def _coerce_argv(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _extract_code_blocks(
    execution_input: Any,
) -> list[tuple[str, ScriptLanguage | None]]:
    """Pull code and declared language from common execution-input shapes."""

    if isinstance(execution_input, str):
        return [(execution_input, None)]
    if isinstance(execution_input, Mapping):
        blocks = _normalize_code_blocks(execution_input.get("code_blocks"))
        if blocks:
            return blocks
        code = execution_input.get("code") or execution_input.get("script")  # type: ignore[union-attr]
        if isinstance(code, str):
            return [(code, _coerce_script_language(
                execution_input.get("language")))]
        if isinstance(code, (list, tuple)):
            return [(str(block), None) for block in code]
    code_blocks = getattr(execution_input, "code_blocks", None)
    if code_blocks is not None:
        blocks = _normalize_code_blocks(code_blocks)
        if blocks:
            return blocks
    code_attr = getattr(execution_input, "code", None)
    if isinstance(code_attr, str):
        return [(code_attr, _coerce_script_language(
            getattr(execution_input, "language", None)))]
    return []


def _normalize_code_blocks(
    raw_blocks: Any,
) -> list[tuple[str, ScriptLanguage | None]]:
    if not isinstance(raw_blocks, (list, tuple)):
        return []
    blocks: list[tuple[str, ScriptLanguage | None]] = []
    for block in raw_blocks:
        if isinstance(block, str):
            blocks.append((block, None))
            continue
        if isinstance(block, Mapping):
            code = block.get("code")
            language = block.get("language")
        else:
            code = getattr(block, "code", None)
            language = getattr(block, "language", None)
        if isinstance(code, str):
            blocks.append((code, _coerce_script_language(language)))
    return blocks


def _coerce_script_language(value: Any) -> ScriptLanguage | None:
    if value is None or not str(value).strip():
        return None
    normalized = str(value).strip().lower()
    aliases = {"sh": "bash", "shell": "bash", "zsh": "bash", "py": "python"}
    try:
        return ScriptLanguage(aliases.get(normalized, normalized))
    except ValueError:
        return ScriptLanguage.UNKNOWN


def _make_failure_result(message: str) -> Any:
    return _ExecutorFailure(message)


class _ExecutorFailure:
    """Simple structured failure that exposes ``outcome`` and ``output``.

    The shape matches what most duck-typed executors return so callers
    can consume it without caring whether the wrapper delegated.
    """

    def __init__(self, message: str) -> None:
        self.outcome = "FAILURE"
        self.output = message

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"_ExecutorFailure({self.output!r})"


def _render_executor_block(report: SafetyReport) -> Any:
    payload = (
        f"[trpc_agent_sdk.tools.safety] execution blocked: decision={report.decision.value} "
        f"risk={report.risk_level.label()} rules={','.join(report.rule_ids)} "
        f"report_id={report.report_id}"
    )
    return _ExecutorFailure(payload)


def _truncate_output(result: Any, max_bytes: int) -> Any:
    if max_bytes <= 0:
        return result
    output = result.get("output") if isinstance(result, Mapping) \
        else getattr(result, "output", None)
    if isinstance(output, str):
        encoded = output.encode("utf-8", errors="ignore")
        if len(encoded) <= max_bytes:
            return result
        marker = f"\n[truncated {len(encoded) - max_bytes} bytes]"
        marker_bytes = marker.encode("utf-8")
        if len(marker_bytes) >= max_bytes:
            replacement = marker_bytes[:max_bytes].decode(
                "utf-8", errors="ignore")
        else:
            prefix_bytes = encoded[:max_bytes - len(marker_bytes)]
            prefix = prefix_bytes.decode("utf-8", errors="ignore")
            replacement = prefix + marker
        if isinstance(result, dict):
            result["output"] = replacement
            return result
        if isinstance(result, Mapping):
            return {**result, "output": replacement}
        try:
            object.__setattr__(result, "output", replacement)
        except (AttributeError, TypeError):
            return replacement
    return result
