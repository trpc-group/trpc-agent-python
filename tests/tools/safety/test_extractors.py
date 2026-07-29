# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for extract_tool_safety_context."""

from __future__ import annotations

from trpc_agent_sdk.tools.safety._extractors import extract_tool_safety_context
from trpc_agent_sdk.tools.safety._types import ScanTarget
from trpc_agent_sdk.tools.safety._types import ScriptLanguage


class DummyTool:
    name = "Bash"


class TestExtractBash:

    def test_extracts_command(self):
        tool = DummyTool()
        req = extract_tool_safety_context(tool, {"command": "rm -rf /", "cwd": "/tmp", "timeout": 30})
        assert req is not None
        assert req.script == "rm -rf /"
        assert req.cwd == "/tmp"
        assert req.language == ScriptLanguage.BASH

    def test_non_executable_returns_none(self):
        tool = DummyTool()
        req = extract_tool_safety_context(tool, {"city": "Tokyo"})
        assert req is None


class TestExtractScript:

    def test_extracts_script(self):
        tool = DummyTool()
        req = extract_tool_safety_context(tool, {"script": "print(1)", "language": "python"})
        assert req is not None
        assert req.script == "print(1)"
        assert req.language == ScriptLanguage.PYTHON

    def test_extracts_code(self):
        tool = DummyTool()
        req = extract_tool_safety_context(tool, {"code": "print(2)", "language": "py"})
        assert req is not None
        assert req.script == "print(2)"
        assert req.language == ScriptLanguage.PYTHON


class TestExtractGeneric:

    def test_extracts_shell_command_key(self):
        tool = DummyTool()
        req = extract_tool_safety_context(tool, {"shell_command": "ls -la /tmp"})
        assert req is not None
        assert req.script == "ls -la /tmp"
        assert req.tool_metadata == {}

    def test_generic_extractor_does_not_treat_business_args_as_metadata(self):
        tool = DummyTool()
        req = extract_tool_safety_context(tool, {
            "shell_command": "echo hello",
            "timeout": 9999,
            "max_output_bytes": 999999999,
        })
        assert req is not None
        assert req.tool_metadata == {}

    def test_short_value_skipped(self):
        tool = DummyTool()
        req = extract_tool_safety_context(tool, {"cmd": "ls"})
        assert req is None  # too short


class TestMCPNoFalsePositive:

    def test_pure_business_params_returns_none(self):
        """MCP Tool with only business parameters should not trigger scan."""
        tool = DummyTool()
        req = extract_tool_safety_context(tool, {"city": "Tokyo", "country": "JP"})
        assert req is None


class TestFileToolPathInterception:

    def test_env_path_intercepted(self):
        """File tool operations on .env should be extracted and scannable."""
        tool = DummyTool()
        req = extract_tool_safety_context(tool, {"command": "cat .env"}, target=ScanTarget.FILE_TOOL)
        assert req is not None
        assert ".env" in req.script
        assert req.target == ScanTarget.FILE_TOOL

    def test_ssh_path_intercepted(self):
        """File tool operations on ~/.ssh should be extracted."""
        tool = DummyTool()
        req = extract_tool_safety_context(tool, {"command": "ls ~/.ssh"}, target=ScanTarget.FILE_TOOL)
        assert req is not None
        assert "~/.ssh" in req.script
        assert req.target == ScanTarget.FILE_TOOL

    def test_etc_path_intercepted(self):
        """File tool operations on /etc should be extracted."""
        tool = DummyTool()
        req = extract_tool_safety_context(tool, {"command": "cat /etc/passwd"}, target=ScanTarget.FILE_TOOL)
        assert req is not None
        assert "/etc" in req.script
        assert req.target == ScanTarget.FILE_TOOL
