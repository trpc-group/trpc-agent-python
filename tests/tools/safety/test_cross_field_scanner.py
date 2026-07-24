"""Tests for trpc_agent_sdk.tools.safety._cross_field_scanner."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety._cross_field_scanner import (
    CrossFieldScannerRule,
    _looks_like_executable,
)
from trpc_agent_sdk.tools.safety._models import (
    RiskCategory,
    SafetyDecision,
    ToolKind,
)


class TestCwdChecks:

    def test_cwd_with_dotdot_denied(self, make_policy, scan_request_factory):
        rule = CrossFieldScannerRule()
        req = scan_request_factory(cwd="/tmp/../../etc")
        out = list(rule.scan(req, make_policy()))
        assert any(f.rule_id == "FILE002_DENIED_WRITE" for f in out)

    def test_cwd_on_denied_path(self, make_policy, scan_request_factory):
        # Policy denies /etc/** which matches /etc/passwd but not /etc.
        rule = CrossFieldScannerRule()
        req = scan_request_factory(cwd="/etc/passwd")
        out = list(rule.scan(req, make_policy()))
        assert any(f.rule_id == "FILE002_DENIED_WRITE" for f in out)

    def test_cwd_safe(self, make_policy, scan_request_factory):
        rule = CrossFieldScannerRule()
        req = scan_request_factory(cwd="/tmp")
        out = list(rule.scan(req, make_policy()))
        assert not any(f.rule_id == "FILE002_DENIED_WRITE" for f in out)

    def test_no_cwd_no_finding(self, make_policy, scan_request_factory):
        rule = CrossFieldScannerRule()
        req = scan_request_factory(cwd=None)
        out = list(rule.scan(req, make_policy()))
        assert not any(f.rule_id == "FILE002_DENIED_WRITE" for f in out)


class TestTimeoutChecks:

    def test_timeout_within_limit(self, make_policy, scan_request_factory):
        rule = CrossFieldScannerRule()
        req = scan_request_factory(requested_timeout_seconds=30.0)
        out = list(rule.scan(req, make_policy()))
        assert not any(f.rule_id == "RES003_LONG_SLEEP" for f in out)

    def test_timeout_exceeds_limit(self, make_policy, scan_request_factory):
        rule = CrossFieldScannerRule()
        req = scan_request_factory(requested_timeout_seconds=120.0)
        out = list(rule.scan(req, make_policy()))
        assert any(f.rule_id == "RES003_LONG_SLEEP" for f in out)

    def test_no_timeout_no_finding(self, make_policy, scan_request_factory):
        rule = CrossFieldScannerRule()
        req = scan_request_factory(requested_timeout_seconds=None)
        out = list(rule.scan(req, make_policy()))
        assert not any(f.rule_id == "RES003_LONG_SLEEP" for f in out)


class TestArgvChecks:

    def test_argv_denied_executable(self, make_policy, scan_request_factory):
        rule = CrossFieldScannerRule()
        req = scan_request_factory(argv=("rm", "-rf", "/x"))
        out = list(rule.scan(req, make_policy()))
        assert any(f.rule_id == "PROC001_PROCESS_EXEC" for f in out)

    def test_argv_unknown_executable_review(self, make_policy, scan_request_factory):
        rule = CrossFieldScannerRule()
        req = scan_request_factory(argv=("unknownexec", ))
        out = list(rule.scan(req, make_policy()))
        assert any(f.rule_id == "PROC001_PROCESS_EXEC" for f in out)

    def test_argv_in_allow_list(self, make_policy, scan_request_factory):
        # Only the executable token is checked; subsequent args are treated
        # as inputs, not executables.
        rule = CrossFieldScannerRule()
        req = scan_request_factory(argv=("python", ))
        out = list(rule.scan(req, make_policy()))
        assert not any(f.rule_id == "PROC001_PROCESS_EXEC" for f in out)

    def test_argv_option_skipped(self, make_policy, scan_request_factory):
        rule = CrossFieldScannerRule()
        req = scan_request_factory(argv=("-x", ))
        out = list(rule.scan(req, make_policy()))
        assert not any(f.rule_id == "PROC001_PROCESS_EXEC" for f in out)


class TestToolMappingCheck:

    def test_unknown_execution_capable_tool(self, make_policy, scan_request_factory):
        rule = CrossFieldScannerRule()
        req = scan_request_factory(
            tool_name="custom_tool",
            metadata={"execution_capable": True},
        )
        out = list(rule.scan(req, make_policy()))
        assert any(f.rule_id == "PARSE001_UNCERTAIN" for f in out)

    def test_known_builtin_adapter_no_finding(self, make_policy, scan_request_factory):
        rule = CrossFieldScannerRule()
        req = scan_request_factory(
            tool_name="workspace_exec",
            metadata={
                "execution_capable": True,
                "adapter_id": "workspace_exec"
            },
        )
        out = list(rule.scan(req, make_policy()))
        assert not any(f.rule_id == "PARSE001_UNCERTAIN" for f in out)

    def test_policy_declared_adapter_no_finding(self, make_policy, scan_request_factory):
        from trpc_agent_sdk.tools.safety._policy import ToolFieldMapping
        from trpc_agent_sdk.tools.safety._models import ScriptLanguage

        policy = make_policy(
            tools={
                "custom_tool": ToolFieldMapping(
                    execution_capable=True,
                    language=ScriptLanguage.PYTHON,
                    script="code",
                ),
            })
        rule = CrossFieldScannerRule()
        req = scan_request_factory(
            tool_name="custom_tool",
            metadata={
                "execution_capable": True,
                "adapter_id": "custom_tool"
            },
        )
        out = list(rule.scan(req, policy))
        assert not any(f.rule_id == "PARSE001_UNCERTAIN" for f in out)

    def test_not_execution_capable_no_finding(self, make_policy, scan_request_factory):
        rule = CrossFieldScannerRule()
        req = scan_request_factory(metadata={"execution_capable": False}, )
        out = list(rule.scan(req, make_policy()))
        assert not any(f.rule_id == "PARSE001_UNCERTAIN" for f in out)


class TestOutputBudgetCheck:

    def test_output_within_budget(self, make_policy, scan_request_factory):
        rule = CrossFieldScannerRule()
        req = scan_request_factory(requested_output_bytes=512)
        out = list(rule.scan(req, make_policy()))
        assert not any(f.rule_id == "RES005_LARGE_WRITE" for f in out)

    def test_output_exceeds_budget(self, make_policy, scan_request_factory):
        rule = CrossFieldScannerRule()
        req = scan_request_factory(requested_output_bytes=4096)
        out = list(rule.scan(req, make_policy()))
        assert any(f.rule_id == "RES005_LARGE_WRITE" for f in out)


class TestLooksLikeExecutable:

    def test_plain_name(self):
        assert _looks_like_executable("python") is True

    def test_dashed_name(self):
        assert _looks_like_executable("python-3") is True

    def test_dotted_name(self):
        assert _looks_like_executable("python.3") is True

    def test_option(self):
        assert _looks_like_executable("-x") is False

    def test_path(self):
        assert _looks_like_executable("/usr/bin/x") is False

    def test_backslash_path(self):
        assert _looks_like_executable("C:\\x") is False

    def test_empty(self):
        assert _looks_like_executable("") is False
