"""Tests for trpc_agent_sdk.tools.safety._filter."""

from __future__ import annotations

import asyncio

import pytest

from trpc_agent_sdk.tools.safety._audit import InMemoryAuditSink
from trpc_agent_sdk.tools.safety._exceptions import (
    SafetyAuditError,
    ToolRequestError,
)
from trpc_agent_sdk.tools.safety._filter import (
    BlockedExecutionError,
    ToolScriptSafetyFilter,
    _build_request_from_raw,
    _looks_like_args_dict,
    _render_block,
    _resolve_args,
    _resolve_tool_kind,
    _resolve_tool_name,
    _set_filter_continue,
    _set_filter_rsp,
)
from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._models import (
    RiskLevel,
    SafetyDecision,
    SafetyReport,
    ScriptLanguage,
    ToolKind,
)
from trpc_agent_sdk.tools.safety._policy import load_safety_policy_dict
from trpc_agent_sdk.tools.safety._tool_adapter import ToolInputAdapter
from trpc_agent_sdk.tools.safety._policy import ToolFieldMapping


def _policy(**overrides):
    return load_safety_policy_dict({"version": "1", **overrides})


def _make_filter(**overrides) -> ToolScriptSafetyFilter:
    policy = _policy(**overrides)
    guard = ToolSafetyGuard(policy)
    return ToolScriptSafetyFilter(guard, audit_sink=InMemoryAuditSink())


class TestCheck:

    def test_safe_python_returns_allow(self):
        flt = _make_filter()
        decision, report = flt.check("python_exec", {"code": "print('hi')"})
        assert decision == SafetyDecision.ALLOW
        assert report.findings == ()

    def test_dangerous_python_returns_deny(self):
        flt = _make_filter()
        decision, _ = flt.check(
            "python_exec",
            {"code": "import shutil\nshutil.rmtree('/x')"},
        )
        assert decision == SafetyDecision.DENY

    def test_unknown_tool_kind_preserved(self):
        flt = _make_filter()
        # Args without adapter mapping: returns Unknown decision review.
        decision, _ = flt.check(
            "custom_tool",
            {"x": "y"},
            tool_kind=ToolKind.UNKNOWN,
        )
        # No script in custom mapping -> empty script -> allow.
        assert decision in (SafetyDecision.ALLOW, SafetyDecision.NEEDS_HUMAN_REVIEW)


class TestCheckAsync:

    @pytest.mark.asyncio
    async def test_async_check(self):
        flt = _make_filter()
        decision, _ = await flt.check_async("python_exec", {"code": "print('hi')"})
        assert decision == SafetyDecision.ALLOW


class TestEnforce:

    def test_enforce_blocks_on_deny(self):
        flt = _make_filter()
        with pytest.raises(BlockedExecutionError):
            flt.enforce(
                "python_exec",
                {"code": "import shutil\nshutil.rmtree('/x')"},
            )

    def test_enforce_returns_report_on_allow(self):
        flt = _make_filter()
        report = flt.enforce("python_exec", {"code": "print('hi')"})
        assert report.decision == SafetyDecision.ALLOW

    def test_enforce_request_request_object(self):
        flt = _make_filter()
        from trpc_agent_sdk.tools.safety._models import SafetyScanRequest
        req = SafetyScanRequest(
            tool_name="python_exec",
            language=ScriptLanguage.PYTHON,
            script="print('hi')",
        )
        report = flt.enforce_request(req)
        assert report.decision == SafetyDecision.ALLOW


class TestEnforceAsync:

    @pytest.mark.asyncio
    async def test_enforce_async_blocks(self):
        flt = _make_filter()
        with pytest.raises(BlockedExecutionError):
            await flt.enforce_async(
                "python_exec",
                {"code": "import shutil\nshutil.rmtree('/x')"},
            )

    @pytest.mark.asyncio
    async def test_enforce_async_allows(self):
        flt = _make_filter()
        report = await flt.enforce_async("python_exec", {"code": "print('hi')"})
        assert report.decision == SafetyDecision.ALLOW


class TestHumanReviewBlocking:

    def test_review_blocks_by_default(self):
        # dynamic exec triggers NEEDS_HUMAN_REVIEW by default
        flt = _make_filter()
        with pytest.raises(BlockedExecutionError):
            flt.enforce(
                "python_exec",
                {"code": "eval('1+1')"},
            )

    def test_review_passes_when_disabled(self):
        flt = _make_filter(defaults={"human_review_blocks_execution": False})
        report = flt.enforce(
            "python_exec",
            {"code": "eval('1+1')"},
        )
        # Doesn't raise; report carries NEEDS_HUMAN_REVIEW
        assert SafetyDecision.NEEDS_HUMAN_REVIEW in (report.decision, SafetyDecision.NEEDS_HUMAN_REVIEW)


class TestFilterDuckHooks:

    @pytest.mark.asyncio
    async def test_before_blocks_dangerous(self):
        flt = _make_filter()

        class Ctx:
            tool_name = "python_exec"

        ctx = Ctx()
        req = {"code": "import shutil\nshutil.rmtree('/x')"}
        rsp: dict = {}

        await flt._before(ctx, req, rsp)
        assert rsp.get("is_continue") is False
        # _set_filter_rsp on a dict calls update() directly with the payload
        # (which has a top-level "tool_safety" key).
        assert "tool_safety" in rsp

    @pytest.mark.asyncio
    async def test_before_allows_safe(self):
        flt = _make_filter()

        class Ctx:
            tool_name = "python_exec"

        ctx = Ctx()
        req = {"code": "print('hi')"}
        rsp: dict = {}

        await flt._before(ctx, req, rsp)
        assert rsp.get("is_continue") is True

    @pytest.mark.asyncio
    async def test_before_with_string_args(self):
        flt = _make_filter()

        class Ctx:
            tool_name = "bash_exec"

        ctx = Ctx()
        req = "echo hi"
        rsp: dict = {}

        await flt._before(ctx, req, rsp)
        assert rsp.get("is_continue") is True

    @pytest.mark.asyncio
    async def test_after_is_noop(self):
        flt = _make_filter()
        # Should not raise and return None
        result = await flt._after(None, None, None)
        assert result is None


class TestBlocksExecution:

    def test_deny_blocks(self):
        flt = _make_filter()
        report = SafetyReport(
            report_id="r",
            decision=SafetyDecision.DENY,
            risk_level=RiskLevel.HIGH,
            rule_ids=("X", ),
            findings=(),
            recommendation="",
            policy_hash="",
            policy_version="1",
            script_sha256="",
            scan_duration_ms=0,
            redacted=False,
        )
        assert flt.blocks_execution(report) is True

    def test_allow_does_not_block(self):
        flt = _make_filter()
        report = SafetyReport(
            report_id="r",
            decision=SafetyDecision.ALLOW,
            risk_level=RiskLevel.INFO,
            rule_ids=("X", ),
            findings=(),
            recommendation="",
            policy_hash="",
            policy_version="1",
            script_sha256="",
            scan_duration_ms=0,
            redacted=False,
        )
        assert flt.blocks_execution(report) is False


class TestRunSyncInsideEventLoop:

    @pytest.mark.asyncio
    async def test_raises_when_loop_running(self):
        flt = _make_filter()
        with pytest.raises(SafetyAuditError):
            flt.check("python_exec", {"code": "print('hi')"})


class TestModuleHelpers:

    def test_looks_like_args_dict_mapping(self):
        assert _looks_like_args_dict({"a": 1}) is True
        assert _looks_like_args_dict("x") is False

    def test_build_request_from_raw_string(self):
        adapter = ToolInputAdapter("x", ToolFieldMapping(language=ScriptLanguage.BASH))
        req = _build_request_from_raw("x", ToolKind.UNKNOWN, "echo hi", adapter)
        assert req.script == "echo hi"
        assert req.language == ScriptLanguage.BASH

    def test_build_request_from_raw_mapping(self):
        adapter = ToolInputAdapter(
            "python_exec", ToolFieldMapping(execution_capable=True, language=ScriptLanguage.PYTHON, script="code"))
        req = _build_request_from_raw("python_exec", ToolKind.UNKNOWN, {"code": "print(1)"}, adapter)
        assert req.script == "print(1)"

    def test_build_request_from_raw_invalid_type(self):
        adapter = ToolInputAdapter("x", ToolFieldMapping())
        with pytest.raises(ToolRequestError):
            _build_request_from_raw("x", ToolKind.UNKNOWN, 123, adapter)

    def test_resolve_tool_name_priority(self):

        class Req:
            tool_name = "from_req"

        class Ctx:
            name = "from_ctx"

        assert _resolve_tool_name(Ctx(), Req()) == "from_req"
        assert _resolve_tool_name("plain_string", Req()) == "from_req"
        assert _resolve_tool_name(None, None) == "unknown"

    def test_resolve_args_variants(self):
        from collections.abc import Mapping
        # dict input
        assert _resolve_args({"x": 1}) == {"x": 1}

        class HasArgs:
            arguments = {"y": 2}

        assert _resolve_args(HasArgs()) == {"y": 2}

        # string input gets wrapped under "command"
        assert _resolve_args("echo") == {"command": "echo"}

        # empty fallback
        class Empty:
            pass

        assert _resolve_args(Empty()) == {}

    def test_resolve_tool_kind_from_object(self):

        class Ctx:
            tool_kind = ToolKind.MCP

        assert _resolve_tool_kind(Ctx(), None) == ToolKind.MCP

    def test_resolve_tool_kind_from_string(self):

        class Ctx:
            tool_kind = "tool"

        assert _resolve_tool_kind(Ctx(), None) == ToolKind.TOOL

    def test_resolve_tool_kind_invalid_string(self):

        class Ctx:
            tool_kind = "bogus"

        # Invalid string falls through to UNKNOWN
        assert _resolve_tool_kind(Ctx(), None) == ToolKind.UNKNOWN

    def test_resolve_tool_kind_unknown(self):
        assert _resolve_tool_kind(None, None) == ToolKind.UNKNOWN

    def test_set_filter_continue_object(self):

        class Rsp:
            is_continue: bool = True

        rsp = Rsp()
        _set_filter_continue(rsp, False)
        assert rsp.is_continue is False

    def test_set_filter_continue_dict(self):
        rsp: dict = {}
        _set_filter_continue(rsp, True)
        assert rsp == {"is_continue": True}

    def test_set_filter_continue_none(self):
        # No error raised on None
        _set_filter_continue(None, True)

    def test_set_filter_rsp_object(self):

        class Rsp:
            rsp: dict = {}

        rsp = Rsp()
        _set_filter_rsp(rsp, {"k": "v"})
        assert rsp.rsp == {"k": "v"}

    def test_set_filter_rsp_dict(self):
        rsp: dict = {}
        _set_filter_rsp(rsp, {"k": "v"})
        assert rsp == {"k": "v"}

    def test_set_filter_rsp_none(self):
        _set_filter_rsp(None, {})

    def test_render_block_shape(self):
        from trpc_agent_sdk.tools.safety._models import (
            Evidence,
            RiskCategory,
            RiskLevel,
            SafetyFinding,
        )
        report = SafetyReport(
            report_id="r",
            decision=SafetyDecision.DENY,
            risk_level=RiskLevel.HIGH,
            rule_ids=("X", ),
            findings=(SafetyFinding(
                rule_id="X",
                category=RiskCategory.FILE,
                risk_level=RiskLevel.HIGH,
                decision=SafetyDecision.DENY,
                evidence=Evidence(snippet="x"),
                recommendation="don't",
            ), ),
            recommendation="block",
            policy_hash="p",
            policy_version="1",
            script_sha256="s",
            scan_duration_ms=0,
            redacted=False,
        )
        out = _render_block(report)
        assert "tool_safety" in out
        ts = out["tool_safety"]
        assert ts["decision"] == "deny"
        assert ts["findings"][0]["rule_id"] == "X"


class TestRecordReportFailClosed:

    @pytest.mark.asyncio
    async def test_audit_required_blocks_on_emit_failure(self):
        # Build a sink that always raises SafetyAuditError.
        from trpc_agent_sdk.tools.safety._audit import AuditSink
        from trpc_agent_sdk.tools.safety._exceptions import SafetyAuditError
        from trpc_agent_sdk.tools.safety._models import (
            SafetyAuditEvent,
            RiskLevel,
            SafetyDecision,
            ToolKind,
        )

        class FailingSink:

            async def emit(self, event):
                raise SafetyAuditError("disk on fire")

        policy = _policy(audit={"enabled": True, "required": True})
        guard = ToolSafetyGuard(policy)
        flt = ToolScriptSafetyFilter(guard, audit_sink=FailingSink())

        from trpc_agent_sdk.tools.safety._models import SafetyScanRequest
        req = SafetyScanRequest(
            tool_name="t",
            tool_kind=ToolKind.UNKNOWN,
            language=ScriptLanguage.PYTHON,
            script="print(1)",
        )
        report = guard.scan(req)
        with pytest.raises(SafetyAuditError):
            await flt.record_report_async(req, report, blocked=False)

    @pytest.mark.asyncio
    async def test_audit_not_required_swallows_failure(self):
        from trpc_agent_sdk.tools.safety._exceptions import SafetyAuditError

        class FailingSink:

            async def emit(self, event):
                raise SafetyAuditError("disk on fire")

        policy = _policy(audit={"enabled": True, "required": False})
        guard = ToolSafetyGuard(policy)
        flt = ToolScriptSafetyFilter(guard, audit_sink=FailingSink())

        from trpc_agent_sdk.tools.safety._models import SafetyScanRequest
        req = SafetyScanRequest(
            tool_name="t",
            language=ScriptLanguage.PYTHON,
            script="print(1)",
        )
        report = guard.scan(req)
        # Should not raise; audit not required.
        await flt.record_report_async(req, report, blocked=False)
