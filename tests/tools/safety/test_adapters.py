# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Safety input adapter tests."""

from pathlib import Path
from types import SimpleNamespace

from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.tools.safety import adapt_code_execution_input
from trpc_agent_sdk.tools.safety import adapt_tool_request
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import ToolMetadata
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy


def _policy():
    return ToolSafetyPolicy(allowed_commands=["echo"], max_timeout_seconds=30)


def test_adapt_bash_tool_clamps_timeout_and_hides_env_values():
    tool = SimpleNamespace(name="workspace_exec", description="exec")

    request = adapt_tool_request(
        tool,
        {
            "command": "echo ok",
            "timeout_sec": 90,
            "env": {
                "TOKEN": "must-not-leak"
            },
        },
        _policy(),
    )

    assert request.requested_timeout_seconds == 90
    assert request.effective_timeout_seconds == 30
    assert request.env_keys == ["TOKEN"]
    assert "must-not-leak" not in request.model_dump_json()


def test_adapt_non_finite_timeout_falls_back_to_policy_limit():
    tool = SimpleNamespace(name="execute_command", description="MCP command")

    request = adapt_tool_request(tool, {"command": "echo ok", "timeout": float("nan")}, _policy())

    assert request.effective_timeout_seconds == 30


def test_adapt_unknown_tool_is_not_applicable():
    tool = SimpleNamespace(name="calculator", description="")

    request = adapt_tool_request(tool, {"value": 1}, _policy())

    assert request.applicable is False
    assert request.payloads == []


def test_adapt_code_execution_input_scans_every_block():
    value = CodeExecutionInput(
        code="print('root')",
        code_blocks=[
            CodeBlock(language="python", code="print('one')"),
            CodeBlock(language="bash", code="echo two"),
        ],
    )

    request = adapt_code_execution_input(value, ToolMetadata(name="executor"), _policy())

    assert len(request.payloads) == 3
    assert request.payloads[-1].language == ScriptLanguage.BASH


def test_adapt_background_and_tty_execution():
    tool = SimpleNamespace(name="workspace_exec", description="exec")
    request = adapt_tool_request(
        tool,
        {
            "command": "echo ok",
            "background": True,
            "tty": True,
        },
        _policy(),
    )
    assert request.background is True
    assert request.tty is True


def test_adapt_generic_mcp_command():
    tool = SimpleNamespace(name="execute_command", description="MCP shell")
    request = adapt_tool_request(
        tool,
        {
            "command": "echo ok",
            "argv": ["value"],
            "timeout": 3,
        },
        _policy(),
    )
    assert request.applicable is True
    assert request.payloads[0].argv == ["value"]
    assert request.timeout_arg_name == "timeout"


def test_adapt_skill_run_command():
    tool = SimpleNamespace(name="skill_run", description="Skill shell")
    request = adapt_tool_request(
        tool,
        {
            "command": "echo ok",
            "cwd": "skills/safety-demo",
            "timeout": 3,
        },
        _policy(),
    )
    assert request.applicable is True
    assert request.payloads[0].language == ScriptLanguage.BASH
    assert request.timeout_arg_name == "timeout"
    assert request.execution_home == str(Path.home())


def test_bash_tool_family_sets_path_context():
    for name in ("workspace_exec", "skill_run", "skill_exec"):
        request = adapt_tool_request(
            SimpleNamespace(name=name, description="shell"),
            {
                "command": "cat ~/.ssh/id_rsa",
                "cwd": "/workspace"
            },
            _policy(),
        )
        assert request.execution_home == str(Path.home())
        assert request.execution_root == Path("/workspace").anchor


def test_unknown_tool_with_code_field_is_scanned_conservatively():
    tool = SimpleNamespace(name="code_formatter", description="formats text")
    request = adapt_tool_request(tool, {"code": "open('/etc/shadow').read()"}, _policy())
    assert request.applicable is True
    assert request.payloads[0].language == ScriptLanguage.PYTHON


def test_unknown_tool_with_command_field_is_scanned_as_bash():
    tool = SimpleNamespace(name="custom_exec", description="custom executor")
    request = adapt_tool_request(tool, {"command": "rm -rf /"}, _policy())
    assert request.applicable is True
    assert request.payloads[0].language == ScriptLanguage.BASH


def test_local_bash_resolves_relative_cwd():
    tool = SimpleNamespace(name="Bash", description="shell", cwd=".")
    request = adapt_tool_request(tool, {"command": "echo ok"}, _policy())
    assert Path(request.cwd).is_absolute()


def test_local_bash_resolves_argument_cwd_from_tool_cwd(tmp_path):
    tool = SimpleNamespace(name="Bash", description="shell", cwd=str(tmp_path))
    request = adapt_tool_request(
        tool,
        {
            "command": "echo ok",
            "cwd": "child",
        },
        _policy(),
    )
    assert request.cwd == str((tmp_path / "child").resolve())
