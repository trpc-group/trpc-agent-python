"""Tests for trpc_agent_sdk.tools.safety._tool_adapter."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety._exceptions import ToolRequestError
from trpc_agent_sdk.tools.safety._models import ScriptLanguage, ToolKind
from trpc_agent_sdk.tools.safety._policy import ToolFieldMapping
from trpc_agent_sdk.tools.safety._tool_adapter import (
    ToolInputAdapter,
    _BUILTIN_DEFAULTS,
    _extract_float,
    _extract_mapping,
    _extract_scalar,
    _extract_sequence,
    build_default_adapters,
    resolve_adapter,
)


class TestExtractScalar:

    def test_returns_none_when_field_missing(self):
        out = _extract_scalar({"a": 1}, "b", required=False)
        assert out is None

    def test_returns_default_for_missing_required(self):
        with pytest.raises(ToolRequestError):
            _extract_scalar({"a": 1}, "b", required=True)

    def test_returns_none_for_none_value(self):
        assert _extract_scalar({"a": None}, "a", required=True) is None

    def test_returns_str_directly(self):
        assert _extract_scalar({"a": "x"}, "a", required=False) == "x"

    def test_joins_list(self):
        out = _extract_scalar({"a": ["ls", "-l"]}, "a", required=False)
        assert out == "ls -l"

    def test_stringifies_other(self):
        out = _extract_scalar({"a": 42}, "a", required=False)
        assert out == "42"

    def test_no_field_name(self):
        assert _extract_scalar({"a": 1}, None, required=True) is None


class TestExtractMapping:

    def test_no_field_name(self):
        assert _extract_mapping({"a": 1}, None) == {}

    def test_missing(self):
        assert _extract_mapping({"a": 1}, "b") == {}

    def test_none_value(self):
        assert _extract_mapping({"a": None}, "a") == {}

    def test_stringifies_kv(self):
        out = _extract_mapping({"a": {"X": 1, "Y": 2}}, "a")
        assert out == {"X": "1", "Y": "2"}

    def test_non_mapping_rejected(self):
        with pytest.raises(ToolRequestError):
            _extract_mapping({"a": ["x"]}, "a")


class TestExtractSequence:

    def test_no_field_name(self):
        assert _extract_sequence({"a": 1}, None) == ()

    def test_missing(self):
        assert _extract_sequence({"a": 1}, "b") == ()

    def test_none(self):
        assert _extract_sequence({"a": None}, "a") == ()

    def test_str_wrapped(self):
        assert _extract_sequence({"a": "ls"}, "a") == ("ls", )

    def test_list_str(self):
        out = _extract_sequence({"a": ["ls", "-l"]}, "a")
        assert out == ("ls", "-l")

    def test_other_stringified(self):
        assert _extract_sequence({"a": 42}, "a") == ("42", )


class TestExtractFloat:

    def test_no_field_name(self):
        assert _extract_float({"a": 1}, None) is None

    def test_missing(self):
        assert _extract_float({"a": 1}, "b") is None

    def test_none(self):
        assert _extract_float({"a": None}, "a") is None

    def test_numeric(self):
        assert _extract_float({"a": 5}, "a") == 5.0
        assert _extract_float({"a": 5.5}, "a") == 5.5

    def test_invalid_returns_none(self):
        assert _extract_float({"a": "abc"}, "a") is None


class TestToolInputAdapter:

    def test_build_request_minimal(self):
        adapter = ToolInputAdapter(
            "test",
            ToolFieldMapping(),
            tool_kind=ToolKind.UNKNOWN,
        )
        req = adapter.build_request({})
        assert req.tool_name == "test"
        assert req.script == ""

    def test_build_request_execution_capable_missing_field(self):
        adapter = ToolInputAdapter(
            "test",
            ToolFieldMapping(execution_capable=True, script="code"),
        )
        with pytest.raises(ToolRequestError):
            adapter.build_request({})

    def test_build_request_extracts_fields(self):
        adapter = ToolInputAdapter(
            "test",
            ToolFieldMapping(
                execution_capable=True,
                language=ScriptLanguage.PYTHON,
                script="code",
                cwd="cwd",
                env="env",
                timeout="timeout",
                argv="argv",
            ),
        )
        req = adapter.build_request({
            "code": "print('hi')",
            "cwd": "/tmp",
            "env": {
                "X": "1"
            },
            "timeout": 30,
            "argv": ["ls", "-l"],
        })
        assert req.language == ScriptLanguage.PYTHON
        assert req.script == "print('hi')"
        assert req.cwd == "/tmp"
        assert req.env == {"X": "1"}
        assert req.requested_timeout_seconds == 30.0
        assert req.argv == ("ls", "-l")
        assert req.metadata["execution_capable"] is True
        assert req.metadata["adapter_id"] == "test"

    def test_metadata_merges_caller_metadata(self):
        adapter = ToolInputAdapter(
            "test",
            ToolFieldMapping(execution_capable=False),
        )
        req = adapter.build_request({}, metadata={"caller": "me"})
        assert req.metadata["caller"] == "me"
        assert req.metadata["adapter_id"] == "test"


class TestBuildDefaultAdapters:

    def test_includes_builtin_names(self, default_policy):
        adapters = build_default_adapters(default_policy)
        for name in ("workspace_exec", "skill_run", "skill_exec", "python_exec", "bash_exec"):
            assert name in adapters

    def test_policy_override_wins(self, make_policy):
        policy = make_policy(tools={
            "workspace_exec": {
                "execution_capable": True,
                "language": "python",
                "script": "code",
            },
        })
        adapters = build_default_adapters(policy)
        adapter = adapters["workspace_exec"]
        assert adapter.mapping.language == ScriptLanguage.PYTHON
        assert adapter.mapping.script == "code"

    def test_custom_tool_added(self, default_policy):
        policy = default_policy.model_copy(update={
            "tools": {
                "custom_tool": ToolFieldMapping(
                    execution_capable=False,
                    language=ScriptLanguage.BASH,
                )
            },
        })
        adapters = build_default_adapters(policy)
        assert "custom_tool" in adapters


class TestResolveAdapter:

    def test_builtin_wins(self, default_policy):
        builtin = build_default_adapters(default_policy)
        adapter = resolve_adapter("workspace_exec", default_policy, builtin=builtin)
        assert adapter.tool_name == "workspace_exec"
        assert adapter.mapping.language == ScriptLanguage.BASH

    def test_policy_only(self, make_policy):
        policy = make_policy(tools={
            "custom_tool": ToolFieldMapping(execution_capable=True, language=ScriptLanguage.PYTHON),
        })
        adapter = resolve_adapter("custom_tool", policy, builtin={})
        assert adapter.mapping.language == ScriptLanguage.PYTHON

    def test_unknown_returns_unknown_mapping(self, default_policy):
        adapter = resolve_adapter("never_declared", default_policy, builtin={})
        assert adapter.mapping.language == ScriptLanguage.UNKNOWN
        assert adapter.mapping.execution_capable is False


def test_builtin_defaults_covered():
    # Sanity check on the constant table.
    assert "workspace_exec" in _BUILTIN_DEFAULTS
    assert _BUILTIN_DEFAULTS["python_exec"].language == ScriptLanguage.PYTHON
