"""Tests for the tool input adapter."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety._exceptions import ToolRequestError
from trpc_agent_sdk.tools.safety._models import ScriptLanguage, ToolKind
from trpc_agent_sdk.tools.safety._tool_adapter import (
    ToolInputAdapter,
    build_default_adapters,
    resolve_adapter,
)
from trpc_agent_sdk.tools.safety._policy import load_safety_policy_dict


def test_workspace_exec_adapter_extracts_command(strict_policy_dict):
    policy = load_safety_policy_dict(strict_policy_dict)
    adapters = build_default_adapters(policy)
    adapter = adapters["workspace_exec"]
    request = adapter.build_request({
        "command": "echo hi",
        "cwd": "/tmp",
        "env": {"FOO": "bar"},
        "timeout_sec": 5,
    })
    assert request.script == "echo hi"
    assert request.language == ScriptLanguage.BASH
    assert request.cwd == "/tmp"
    assert request.env == {"FOO": "bar"}
    assert request.requested_timeout_seconds == 5


def test_execution_capable_missing_script_raises(strict_policy_dict):
    policy = load_safety_policy_dict(strict_policy_dict)
    adapters = build_default_adapters(policy)
    adapter = adapters["workspace_exec"]
    with pytest.raises(ToolRequestError):
        adapter.build_request({"cwd": "/tmp"})


def test_custom_tool_adapter_via_policy():
    policy = load_safety_policy_dict({
        "tools": {
            "weird_runner": {
                "execution_capable": True,
                "language": "python",
                "script": "code",
                "env": "environment",
            }
        }
    })
    adapter = policy.tools["weird_runner"]
    assert adapter.execution_capable
    assert adapter.language == ScriptLanguage.PYTHON
    assert adapter.script == "code"
    assert adapter.env == "environment"


def test_resolve_adapter_returns_builtin(strict_policy_dict):
    policy = load_safety_policy_dict(strict_policy_dict)
    adapters = build_default_adapters(policy)
    adapter = resolve_adapter("workspace_exec", policy, builtin=adapters)
    assert adapter.tool_name == "workspace_exec"


def test_resolve_adapter_unknown_returns_unknown_language(strict_policy_dict):
    policy = load_safety_policy_dict(strict_policy_dict)
    adapter = resolve_adapter("never_seen_before", policy,
                              builtin=build_default_adapters(policy))
    assert adapter.mapping.language == ScriptLanguage.UNKNOWN
    assert not adapter.mapping.execution_capable
