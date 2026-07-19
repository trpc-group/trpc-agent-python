# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Wrapper helpers for attaching safety scanning without modifying SDK internals."""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Optional

from ._audit import AuditLogger
from ._filter import ToolSafetyFilter
from ._policy import PolicyConfig
from ._scanner import SafetyScanner
from ._types import Decision
from ._types import ScanInput
from ._types import decision_rank
from ._types import risk_order

# Use the real CodeExecutionResult / Outcome from trpc_agent_sdk.types so deny
# results flow through the Agent pipeline (Part.from_code_execution_result,
# _openai_model._post_process_code_execution_result) without AttributeError on
# .outcome.value. trpc_agent_sdk.types pulls google.genai.types (always
# installed with the SDK core), NOT docker, so this stays dep-light.
try:  # pragma: no cover
    from trpc_agent_sdk.types import CodeExecutionResult
    from trpc_agent_sdk.types import Outcome
    _REAL_RESULT_TYPES_AVAILABLE = True
except Exception as ex:  # pylint: disable=broad-except
    CodeExecutionResult = None  # type: ignore[assignment]
    Outcome = None  # type: ignore[assignment]
    _REAL_RESULT_TYPES_AVAILABLE = False
    _RESULT_IMPORT_ERROR = ex

_logger = logging.getLogger("trpc_agent_sdk.safety")


def wrap_tool(tool, policy: PolicyConfig, *, audit_path: Optional[str] = None):
    """Return *tool* with a :class:`ToolSafetyFilter` prepended to its filters."""
    safety_filter = ToolSafetyFilter(policy=policy, audit_path=audit_path, tool_name=getattr(tool, "name", "tool"))
    tool.add_one_filter(safety_filter, force=True)
    return tool


def _deny_code_result(rule_ids: list[str]):
    """Build a failed CodeExecutionResult using the real SDK types.

    Falls back to a minimal stand-in only if ``trpc_agent_sdk.types`` cannot be
    imported (e.g. google-genai missing), which would itself break the whole
    SDK. The real ``Outcome.OUTCOME_FAILED`` is an enum with ``.value`` and
    ``.name``, so downstream code (Part.from_code_execution_result,
    _openai_model) that reads ``.outcome.value`` works correctly.
    """
    msg = f"Code execution error:\nTOOL_SAFETY_DENY: {rule_ids}\n"
    if _REAL_RESULT_TYPES_AVAILABLE:
        return CodeExecutionResult(outcome=Outcome.OUTCOME_FAILED, output=msg)
    # Last-resort fallback (SDK core itself is broken). Emit a clear error so
    # the misconfiguration is diagnosable instead of silently producing a
    # stub object that crashes downstream pipeline code.
    _logger.error(
        "trpc_agent_sdk.types import failed; cannot build real "
        "CodeExecutionResult. Deny result will use a minimal stub. "
        "Original error: %r",
        _RESULT_IMPORT_ERROR,
    )

    class _Outcome:
        def __init__(self, name: str):
            self.name = name

    class _CodeExecutionResult:
        def __init__(self, *, output: str, outcome_name: str):
            self.output = output
            self.outcome = _Outcome(outcome_name)

    return _CodeExecutionResult(output=msg, outcome_name="OUTCOME_FAILED")


def _normalize_block_language(raw_lang: str | None, code: str) -> str:
    """Resolve code-block language without forcing python for unknown/empty."""
    from ._ast_utils import normalize_language

    raw = (raw_lang or "").strip().lower()
    if raw in ("sh", "shell", "bash"):
        return "bash"
    if "py" in raw or raw == "python":
        return "python"
    if raw == "bash":
        return "bash"
    return normalize_language(ScanInput(script=code or "", language=""))


def _scan_code_input(scanner: SafetyScanner, input_data):
    """Scan code / code_blocks with per-block language; return worst report.

    ``code_blocks`` elements may be either objects (with ``.code`` /
    ``.language`` attributes, e.g. ``CodeBlock``) or dicts (with ``code`` /
    ``language`` keys, as produced by some tool adapters). Both forms are
    supported so the scanner does not silently skip dict blocks (which would
    leave real code unscanned).
    """
    # Use shared ranking from _types so this aggregation logic cannot drift
    # from the executor's copy. Decision severity: DENY > NEEDS_HUMAN_REVIEW >
    # ALLOW. A MEDIUM bash block must outweigh a safe ALLOW python block.
    blocks = list(getattr(input_data, "code_blocks", None) or [])
    top_code = getattr(input_data, "code", None) or ""
    if not blocks and top_code:
        top_lang = getattr(input_data, "language", None) or ""
        blocks = [{"code": top_code, "language": top_lang}]

    def _block_code(block) -> str:
        if isinstance(block, dict):
            return str(block.get("code", "") or "")
        return str(getattr(block, "code", None) or "")

    def _block_lang(block) -> str:
        if isinstance(block, dict):
            return str(block.get("language", "") or "").strip().lower()
        return str(getattr(block, "language", None) or "").strip().lower()

    worst = None
    for block in blocks:
        code = _block_code(block)
        declared = _block_lang(block)
        # Mislabeled bash-as-python must still hit bash rules via content check.
        if declared in ("sh", "shell", "bash"):
            lang = "bash"
        elif declared in ("python", ) or "py" in declared:
            inferred = _normalize_block_language("", code)
            lang = "bash" if inferred == "bash" else "python"
        else:
            lang = _normalize_block_language(declared, code)
        report = scanner.scan(ScanInput(script=code, language=lang, tool_name="code_executor"))
        if worst is None:
            worst = report
            continue
        # Pick the worse of (worst, report) by (decision_rank, risk_order).
        worst_key = (decision_rank(worst.decision), risk_order(worst.risk_level))
        report_key = (decision_rank(report.decision), risk_order(report.risk_level))
        if report_key > worst_key:
            worst = report
    return worst


def _should_block_report(report, block_on_review: bool) -> bool:
    if report is None:
        return False
    return report.decision == Decision.DENY or (report.decision == Decision.NEEDS_HUMAN_REVIEW and block_on_review)


class SafetyGuardedCodeExecutor:
    """Code-executor wrapper that scans code before delegating.

    Does **not** import ``trpc_agent_sdk.code_executors`` (avoids optional docker).
    Deny results use a minimal object with ``.output`` / ``.outcome.name``.
    """

    def __init__(
        self,
        inner,
        policy: PolicyConfig,
        *,
        audit_path: Optional[str] = None,
        block_on_review: bool = False,
    ):
        self._inner = inner
        self._scanner = SafetyScanner(policy=policy)
        self._audit = AuditLogger(audit_path)
        self._block_on_review = block_on_review or policy.block_on_review

    async def execute_code(self, invocation_context, input_data):
        report = _scan_code_input(self._scanner, input_data)
        should_block = _should_block_report(report, self._block_on_review)
        if report is not None:
            self._audit.log(report, intercepted=should_block)
        if should_block and report is not None:
            return _deny_code_result(report.rule_ids)
        return await self._inner.execute_code(invocation_context, input_data)


def safe_code_executor(
    inner,
    policy: PolicyConfig,
    *,
    audit_path: Optional[str] = None,
    block_on_review: bool = False,
):
    """Create a code-executor wrapper that scans code before delegating.

    Returns a simple object with ``execute_code`` (does not subclass
    BaseCodeExecutor, to avoid importing optional code_executors deps).
    """
    scanner = SafetyScanner(policy=policy)
    audit = AuditLogger(audit_path)
    block_review = block_on_review or policy.block_on_review

    class _SafeCodeExecutor:

        async def execute_code(self, invocation_context, input_data):
            report = _scan_code_input(scanner, input_data)
            should_block = _should_block_report(report, block_review)
            if report is not None:
                audit.log(report, intercepted=should_block)
            if should_block and report is not None:
                return _deny_code_result(report.rule_ids)
            return await inner.execute_code(invocation_context, input_data)

    return _SafeCodeExecutor()


# Backwards-compatible alias. SafeCodeExecutor is exposed as a "class-like"
# callable that returns an object with execute_code; it is intentionally a
# factory function (not a class) so we can avoid importing the optional
# code_executors package. Existing code that writes ``SafeCodeExecutor(inner,
# policy)`` continues to work.
SafeCodeExecutor = safe_code_executor


class SafetyDeniedError(RuntimeError):
    """Raised when a safety wrapper blocks a script (decision == DENY)."""

    def __init__(self, report):
        self.report = report
        rule_ids = report.rule_ids if report.rule_ids else ["unknown"]
        super().__init__(f"script denied by rule(s) {rule_ids}")


def safety_wrapper(
    tool_name="unknown",
    *,
    script_arg="script",
    policy=None,
    audit_path=None,
    raise_on_deny=True,
):
    """Decorator: scan the *script_arg* of a function before it runs.

    .. note::
        Only keyword arguments are scanned. Positional arguments are not
        mapped to *script_arg* (doing so reliably would require inspecting
        the wrapped function's signature, which is brittle for *args/**kwargs
        variadic callables). Callers MUST pass the script as a keyword
        argument, e.g. ``run(script="rm -rf /")`` rather than
        ``run("rm -rf /")``. A positional argument that is itself a dict
        containing *script_arg* is also accepted as a legacy convenience.
    """
    if policy is None:
        policy = PolicyConfig()
    _scanner = SafetyScanner(policy=policy)
    _audit = AuditLogger(audit_path)

    def _extract_script(args, kwargs):
        script = kwargs.get(script_arg)
        if script is None:
            for arg in args:
                if isinstance(arg, dict) and script_arg in arg:
                    return arg[script_arg]
        return script

    def _guard(args, kwargs):
        script = _extract_script(args, kwargs)
        if not script or not isinstance(script, str):
            return
        report = _scanner.scan(ScanInput(script=script, tool_name=tool_name))
        # intercepted must reflect the actual interception below, not
        # report.blocked (which uses policy.block_on_review). safety_wrapper
        # only blocks on DENY when raise_on_deny=True; review is never blocked.
        intercepted = report.decision == Decision.DENY and raise_on_deny
        _audit.log(report, intercepted=intercepted)
        if intercepted:
            raise SafetyDeniedError(report)

    def decorator(func):

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            _guard(args, kwargs)
            return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            _guard(args, kwargs)
            return func(*args, **kwargs)

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


class SafetyReviewedSkillRunner:
    """Wrap a skill runner callable with pre-execution safety scanning."""

    def __init__(
        self,
        runner,
        policy,
        *,
        audit_path=None,
        block_review=False,
        tool_name="skill_run",
    ):
        self._runner = runner
        self._scanner = SafetyScanner(policy=policy)
        self._audit = AuditLogger(audit_path)
        self._block_review = block_review or getattr(policy, "block_on_review", False)
        self._tool_name = tool_name

    async def run(self, tool_context, args):
        """Scan skill args and delegate to the wrapped runner when allowed."""
        script = self._extract_script(args)
        if script:
            report = self._scanner.scan(ScanInput(script=script, tool_name=self._tool_name))
            # intercepted must reflect the actual interception below, not
            # report.blocked (which uses policy.block_on_review). Reuse
            # _should_block_report so audit matches the real control flow.
            intercepted = _should_block_report(report, self._block_review)
            self._audit.log(report, intercepted=intercepted)
            if report.decision == Decision.DENY:
                return {
                    "success": False,
                    "error": "SKILL_BLOCKED",
                    "safety": report.to_dict(),
                }
            if report.decision == Decision.NEEDS_HUMAN_REVIEW and self._block_review:
                return {
                    "success": False,
                    "error": "SKILL_NEEDS_REVIEW",
                    "safety": report.to_dict(),
                }

        if hasattr(self._runner, "run_async"):
            result = self._runner.run_async(tool_context=tool_context, args=args)
        else:
            result = self._runner(tool_context, args)
        if hasattr(result, "__await__"):
            return await result
        return result

    @staticmethod
    def _extract_script(args):
        if not isinstance(args, dict):
            return None
        for key in ("script", "code", "command", "cmd"):
            val = args.get(key)
            if isinstance(val, str):
                return val
        return None
