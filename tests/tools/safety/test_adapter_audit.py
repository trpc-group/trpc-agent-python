#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

import json
from pathlib import Path

import pytest

from trpc_agent_sdk.tools.safety import default_request_extractor
from trpc_agent_sdk.tools.safety import JsonlAuditSink
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import ScriptPayload
from trpc_agent_sdk.tools.safety import ScriptScanRequest
from trpc_agent_sdk.tools.safety import ToolMetadata
from trpc_agent_sdk.tools.safety import ToolSafetyGuard


def test_request_extractor_normalizes_supported_tool_argument_shapes():
    request = default_request_extractor(
        {
            "code_blocks": [{
                "language": "py",
                "code": "print('hello')",
            }],
            "command": "echo",
            "args": "hello",
            "stdin": "input",
            "env": None,
            "timeout_seconds": 12,
            "max_output_bytes": 128,
            "cwd": "/workspace",
        },
        tool_name="shell_runner",
    )

    assert request is not None
    assert [payload.language for payload in request.payloads] == [ScriptLanguage.PYTHON, ScriptLanguage.BASH]
    assert request.payloads[1].argv == ["hello"]
    assert request.payloads[1].stdin == "input"
    assert request.env == {}
    assert request.requested_timeout == 12
    assert request.max_output_bytes == 128
    assert request.cwd == "/workspace"


def test_request_extractor_returns_none_for_non_script_tool_calls():
    assert default_request_extractor({"query": "hello"}, tool_name="search") is None


def test_request_extractor_accepts_none_for_optional_argument_list():
    request = default_request_extractor(
        {
            "script": "echo hello",
            "args": None,
        },
        tool_name="shell",
    )

    assert request is not None
    assert request.payloads[0].argv == []


@pytest.mark.parametrize(
    ("arguments", "error_type", "message"),
    [
        (["not", "a", "mapping"], TypeError, "must be a mapping"),
        ({
            "code_blocks": "print('hello')"
        }, TypeError, "code_blocks must be a list"),
        ({
            "code_blocks": [{}]
        }, TypeError, "must contain string code"),
        ({
            "script": "echo hello",
            "env": ["TOKEN=value"]
        }, TypeError, "env must map strings to strings"),
        ({
            "script": "echo hello",
            "timeout": "5"
        }, TypeError, "timeout must be numeric"),
        ({
            "script": "echo hello",
            "max_output_bytes": 1.5
        }, TypeError, "max_output_bytes must be an integer"),
        ({
            "script": "echo hello",
            "language": 7
        }, TypeError, "language must be a string"),
        ({
            "script": "echo hello",
            "language": "ruby"
        }, ValueError, "unsupported script language"),
        ({
            "script": "echo hello",
            "cwd": 7
        }, TypeError, "cwd must be a string"),
        ({
            "script": "echo hello",
            "args": ["valid", 7]
        }, TypeError, "must be a string or list of strings"),
    ],
)
def test_request_extractor_rejects_ambiguous_or_unsafe_shapes(arguments, error_type, message):
    with pytest.raises(error_type, match=message):
        default_request_extractor(arguments, tool_name="shell")


def test_jsonl_audit_sink_persists_one_redacted_event_per_decision(tmp_path: Path):
    audit_path = tmp_path / "audit" / "tool-safety.jsonl"
    guard = ToolSafetyGuard(audit_sink=JsonlAuditSink(audit_path))
    secret = "super-secret-token"
    request = ScriptScanRequest(
        payloads=[ScriptPayload(language=ScriptLanguage.BASH, content=f"echo {secret}")],
        env={"API_TOKEN": secret},
        metadata=ToolMetadata(name="shell"),
    )

    first_report = guard.check(request)
    second_report = guard.check(request)
    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

    assert first_report.decision == second_report.decision
    assert len(events) == 2
    assert [event["tool_name"] for event in events] == ["shell", "shell"]
    assert all(secret not in json.dumps(event) for event in events)
