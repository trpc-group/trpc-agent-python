# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.langfuse.tracing.opentelemetry.

Covers:
- LangfuseConfig dataclass defaults
- _LangfuseMixin: _should_skip_span, _transform_span_for_langfuse, and all
  attribute-mapping methods (new + old versions)
- _LangfuseSpanProcessor / _LangfuseBatchSpanProcessor: on_end delegation
- _LangfuseOTLPExporter: init / export
- setup(): env-var fallback, missing-credentials error, batch vs simple processor
"""

from __future__ import annotations

import base64
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from opentelemetry.sdk.trace import ReadableSpan

import trpc_agent_sdk.server.langfuse.tracing.opentelemetry as otel_module
from trpc_agent_sdk.server.langfuse.tracing.opentelemetry import (
    LangfuseConfig,
    _LangfuseBatchSpanProcessor,
    _LangfuseMixin,
    _LangfuseOTLPExporter,
    _LangfuseSpanProcessor,
    setup,
)
from trpc_agent_sdk.tools import AGENT_TOOL_APP_NAME_SUFFIX


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SPAN_PREFIX = "trpc.python.agent"  # default returned by get_trpc_agent_span_name()


def _make_span(
    name: str = "test_span",
    attributes: dict | None = None,
    scope_name: str | None = None,
):
    """Build a lightweight mock ReadableSpan."""
    span = MagicMock()
    span.name = name
    span.attributes = attributes or {}
    if scope_name is not None:
        span.instrumentation_scope = SimpleNamespace(name=scope_name)
    else:
        span.instrumentation_scope = None
    span.get_span_context.return_value = MagicMock()
    span.parent = None
    span.resource = MagicMock()
    span.events = []
    span.links = []
    span.kind = MagicMock()
    span.status = MagicMock()
    span.start_time = 0
    span.end_time = 1
    return span


@pytest.fixture(autouse=True)
def _set_global_config():
    """Ensure a default LangfuseConfig is set before every test and cleaned up after."""
    original = otel_module._langfuse_config
    otel_module._langfuse_config = LangfuseConfig()
    yield
    otel_module._langfuse_config = original


@pytest.fixture
def mixin():
    """A bare _LangfuseMixin instance for testing attribute-mapping methods."""
    return _LangfuseMixin()


# ---------------------------------------------------------------------------
# LangfuseConfig
# ---------------------------------------------------------------------------
class TestLangfuseConfig:
    """Tests for LangfuseConfig dataclass."""

    def test_defaults(self):
        cfg = LangfuseConfig()
        assert cfg.public_key is None
        assert cfg.secret_key is None
        assert cfg.host is None
        assert cfg.batch_export is True
        assert cfg.compatibility_old_version is False
        assert cfg.enable_a2a_trace is False

    def test_custom_values(self):
        cfg = LangfuseConfig(
            public_key="pk",
            secret_key="sk",
            host="https://h",
            batch_export=False,
            compatibility_old_version=True,
            enable_a2a_trace=True,
        )
        assert cfg.public_key == "pk"
        assert cfg.secret_key == "sk"
        assert cfg.host == "https://h"
        assert cfg.batch_export is False
        assert cfg.compatibility_old_version is True
        assert cfg.enable_a2a_trace is True


# ---------------------------------------------------------------------------
# _LangfuseMixin._should_skip_span
# ---------------------------------------------------------------------------
class TestShouldSkipSpan:
    """Tests for _should_skip_span."""

    def test_enable_a2a_trace_skips_nothing(self, mixin):
        otel_module._langfuse_config = LangfuseConfig(enable_a2a_trace=True)
        span = _make_span(scope_name="a2a-python-sdk")
        assert mixin._should_skip_span(span) is False

    def test_no_instrumentation_scope_not_skipped(self, mixin):
        span = _make_span(scope_name=None)
        assert mixin._should_skip_span(span) is False

    def test_a2a_sdk_scope_skipped(self, mixin):
        span = _make_span(scope_name="a2a-python-sdk")
        assert mixin._should_skip_span(span) is True

    @pytest.mark.parametrize("scope", [
        "opentelemetry.instrumentation.httpx",
        "opentelemetry.instrumentation.urllib3",
        "opentelemetry.instrumentation.requests",
        "opentelemetry.instrumentation.asgi",
        "opentelemetry.instrumentation.fastapi",
        "opentelemetry.instrumentation.fastapi.extra",
    ])
    def test_otel_instrumentation_scopes_skipped(self, mixin, scope):
        span = _make_span(scope_name=scope)
        assert mixin._should_skip_span(span) is True

    @pytest.mark.parametrize("span_name", [
        "HTTP /api/test",
        "GET /resource",
        "POST /resource",
        "PUT /resource",
        "DELETE /resource",
        "PATCH /resource",
        "HEAD /resource",
        "OPTIONS /resource",
    ])
    def test_http_method_spans_skipped(self, mixin, span_name):
        span = _make_span(name=span_name, scope_name="custom.scope")
        assert mixin._should_skip_span(span) is True

    def test_normal_span_not_skipped(self, mixin):
        span = _make_span(name="my_operation", scope_name="my.app")
        assert mixin._should_skip_span(span) is False


# ---------------------------------------------------------------------------
# _LangfuseMixin._transform_span_for_langfuse
# ---------------------------------------------------------------------------
class TestTransformSpanForLangfuse:
    """Tests for _transform_span_for_langfuse."""

    def test_invocation_span_uses_runner_name(self, mixin):
        span = _make_span(
            name="invocation",
            attributes={f"{SPAN_PREFIX}.runner.name": "my_runner"},
            scope_name="trpc",
        )
        result = mixin._transform_span_for_langfuse(span)
        assert result.name == "my_runner"

    def test_invocation_span_defaults_to_unknown(self, mixin):
        span = _make_span(name="invocation", attributes={}, scope_name="trpc")
        result = mixin._transform_span_for_langfuse(span)
        assert result.name == "unknown"

    def test_non_invocation_span_keeps_name(self, mixin):
        span = _make_span(name="call_llm", attributes={}, scope_name="trpc")
        result = mixin._transform_span_for_langfuse(span)
        assert result.name == "call_llm"

    def test_compatibility_old_version_uses_old_mapping(self, mixin):
        otel_module._langfuse_config = LangfuseConfig(compatibility_old_version=True)
        span = _make_span(
            name="my_span",
            attributes={"gen_ai.operation.name": "run_runner", f"{SPAN_PREFIX}.runner.name": "r"},
            scope_name="trpc",
        )
        result = mixin._transform_span_for_langfuse(span)
        assert "langfuse.trace.name" in result.attributes

    def test_agent_tool_span_removes_trace_name(self, mixin):
        app_name = f"sub_agent{AGENT_TOOL_APP_NAME_SUFFIX}"
        span = _make_span(
            name="invocation",
            attributes={
                "gen_ai.operation.name": "run_runner",
                f"{SPAN_PREFIX}.runner.name": "sub_agent",
                f"{SPAN_PREFIX}.runner.app_name": app_name,
            },
            scope_name="trpc",
        )
        result = mixin._transform_span_for_langfuse(span)
        assert "langfuse.trace.name" not in result.attributes

    def test_non_agent_tool_span_keeps_trace_name(self, mixin):
        span = _make_span(
            name="invocation",
            attributes={
                "gen_ai.operation.name": "run_runner",
                f"{SPAN_PREFIX}.runner.name": "main_runner",
                f"{SPAN_PREFIX}.runner.app_name": "main_app",
            },
            scope_name="trpc",
        )
        result = mixin._transform_span_for_langfuse(span)
        assert result.attributes.get("langfuse.trace.name") == "main_runner"


# ---------------------------------------------------------------------------
# _LangfuseMixin._map_attributes_to_langfuse — routing
# ---------------------------------------------------------------------------
class TestMapAttributesToLangfuse:
    """Tests for _map_attributes_to_langfuse operation routing."""

    def test_run_runner_operation(self, mixin):
        attrs = {
            "gen_ai.operation.name": "run_runner",
            f"{SPAN_PREFIX}.runner.name": "my_runner",
        }
        result = mixin._map_attributes_to_langfuse(attrs)
        assert result.get("langfuse.trace.name") == "my_runner"

    def test_run_runner_cancelled_operation(self, mixin):
        attrs = {
            "gen_ai.operation.name": "run_runner_cancelled",
            f"{SPAN_PREFIX}.runner.name": "cancelled_runner",
        }
        result = mixin._map_attributes_to_langfuse(attrs)
        assert result.get("langfuse.trace.name") == "cancelled_runner"

    def test_run_agent_operation(self, mixin):
        attrs = {
            "gen_ai.operation.name": "run_agent",
            f"{SPAN_PREFIX}.agent.input": "hello",
        }
        result = mixin._map_attributes_to_langfuse(attrs)
        assert result["langfuse.observation.type"] == "span"
        assert result["langfuse.observation.input"] == "hello"

    def test_call_llm_operation(self, mixin):
        attrs = {
            "gen_ai.operation.name": "call_llm",
            f"{SPAN_PREFIX}.llm_request": '{"config": {"temperature": 0.5}}',
            f"{SPAN_PREFIX}.llm_response": '{"text": "hi"}',
        }
        result = mixin._map_attributes_to_langfuse(attrs)
        assert result["langfuse.observation.type"] == "generation"

    def test_execute_tool_operation(self, mixin):
        attrs = {
            "gen_ai.operation.name": "execute_tool",
            f"{SPAN_PREFIX}.tool_call_args": "{}",
        }
        result = mixin._map_attributes_to_langfuse(attrs)
        assert result["langfuse.observation.type"] == "span"

    def test_unknown_operation_defaults_to_span(self, mixin):
        attrs = {"gen_ai.operation.name": "some_custom_op", "custom_key": "v"}
        result = mixin._map_attributes_to_langfuse(attrs)
        assert result["langfuse.observation.type"] == "span"
        assert result.get("custom_key") == "v"

    def test_empty_operation_defaults_to_span(self, mixin):
        attrs = {"foo": "bar"}
        result = mixin._map_attributes_to_langfuse(attrs)
        assert result["langfuse.observation.type"] == "span"


# ---------------------------------------------------------------------------
# _map_trace_level_attributes
# ---------------------------------------------------------------------------
class TestMapTraceLevelAttributes:
    """Tests for _map_trace_level_attributes (new format)."""

    def test_basic_trace_attributes(self, mixin):
        attrs = {f"{SPAN_PREFIX}.runner.name": "my_runner"}
        result = mixin._map_trace_level_attributes(attrs)
        assert result["langfuse.trace.name"] == "my_runner"
        assert result["langfuse.observation.type"] == "span"

    def test_user_and_session_id_mapped(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.runner.name": "r",
            f"{SPAN_PREFIX}.runner.user_id": "u1",
            f"{SPAN_PREFIX}.runner.session_id": "s1",
        }
        result = mixin._map_trace_level_attributes(attrs)
        assert result["langfuse.user.id"] == "u1"
        assert result["langfuse.session.id"] == "s1"

    def test_input_output_mapped(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.runner.name": "r",
            f"{SPAN_PREFIX}.runner.input": "in",
            f"{SPAN_PREFIX}.runner.output": "out",
        }
        result = mixin._map_trace_level_attributes(attrs)
        assert result["langfuse.trace.input"] == "in"
        assert result["langfuse.observation.input"] == "in"
        assert result["langfuse.trace.output"] == "out"
        assert result["langfuse.observation.output"] == "out"

    def test_state_metadata(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.runner.name": "r",
            f"{SPAN_PREFIX}.state.begin": "s0",
            f"{SPAN_PREFIX}.state.end": "s1",
            f"{SPAN_PREFIX}.state.partial": "sp",
        }
        result = mixin._map_trace_level_attributes(attrs)
        md = json.loads(result["langfuse.trace.metadata"])
        assert md["state_begin"] == "s0"
        assert md["state_end"] == "s1"
        assert md["state_partial"] == "sp"

    def test_cancellation_metadata(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.runner.name": "r",
            f"{SPAN_PREFIX}.cancellation.reason": "user_request",
            f"{SPAN_PREFIX}.cancellation.agent_name": "agent_a",
            f"{SPAN_PREFIX}.cancellation.branch": "b1",
        }
        result = mixin._map_trace_level_attributes(attrs)
        md = json.loads(result["langfuse.trace.metadata"])
        assert md["cancellation_reason"] == "user_request"
        assert md["cancellation_agent_name"] == "agent_a"
        assert md["cancellation_branch"] == "b1"

    def test_runner_attributes_in_metadata(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.runner.name": "r",
            f"{SPAN_PREFIX}.runner.custom_field": "val",
        }
        result = mixin._map_trace_level_attributes(attrs)
        md = json.loads(result["langfuse.trace.metadata"])
        assert md["name"] == "r"
        assert md["custom_field"] == "val"

    def test_no_metadata_when_empty(self, mixin):
        attrs = {f"{SPAN_PREFIX}.runner.name": "r"}
        result = mixin._map_trace_level_attributes(attrs)
        md = json.loads(result["langfuse.trace.metadata"])
        assert md["name"] == "r"

    def test_no_user_id_or_session_id(self, mixin):
        attrs = {f"{SPAN_PREFIX}.runner.name": "r"}
        result = mixin._map_trace_level_attributes(attrs)
        assert "langfuse.user.id" not in result
        assert "langfuse.session.id" not in result

    def test_no_input_output(self, mixin):
        attrs = {f"{SPAN_PREFIX}.runner.name": "r"}
        result = mixin._map_trace_level_attributes(attrs)
        assert "langfuse.trace.input" not in result
        assert "langfuse.trace.output" not in result


# ---------------------------------------------------------------------------
# _map_agent_observation_attributes
# ---------------------------------------------------------------------------
class TestMapAgentObservationAttributes:
    """Tests for _map_agent_observation_attributes (new format)."""

    def test_agent_input_output_mapped(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.agent.input": "user_query",
            f"{SPAN_PREFIX}.agent.output": "response",
        }
        result = mixin._map_agent_observation_attributes(attrs)
        assert result["langfuse.observation.type"] == "span"
        assert result["langfuse.observation.input"] == "user_query"
        assert result["langfuse.observation.output"] == "response"

    def test_defaults_when_missing(self, mixin):
        result = mixin._map_agent_observation_attributes({})
        assert result["langfuse.observation.input"] == ""
        assert result["langfuse.observation.output"] == ""


# ---------------------------------------------------------------------------
# _map_generation_attributes
# ---------------------------------------------------------------------------
class TestMapGenerationAttributes:
    """Tests for _map_generation_attributes (new format)."""

    def _llm_attrs(self, **overrides):
        base = {
            f"{SPAN_PREFIX}.llm_request": json.dumps({"config": {"temperature": 0.8}, "messages": []}),
            f"{SPAN_PREFIX}.llm_response": json.dumps({"text": "hi"}),
            "gen_ai.usage.input_tokens": "10",
            "gen_ai.usage.output_tokens": "20",
        }
        base.update(overrides)
        return base

    def test_basic_generation_mapping(self, mixin):
        result = mixin._map_generation_attributes(self._llm_attrs())
        assert result["langfuse.observation.type"] == "generation"
        assert "temperature" in result["langfuse.observation.model.parameters"]
        assert result["gen_ai.usage.input_tokens"] == "10"
        assert result["gen_ai.usage.output_tokens"] == "20"

    def test_input_output_from_llm_request_response(self, mixin):
        attrs = self._llm_attrs()
        result = mixin._map_generation_attributes(attrs)
        assert result["langfuse.observation.input"] == attrs[f"{SPAN_PREFIX}.llm_request"]
        assert result["langfuse.observation.output"] == attrs[f"{SPAN_PREFIX}.llm_response"]

    def test_defaults_when_no_llm_request(self, mixin):
        result = mixin._map_generation_attributes({})
        assert result["langfuse.observation.input"] == "unknown"
        assert result["langfuse.observation.output"] == "unknown"
        assert result["langfuse.observation.model.parameters"] == "{}"

    def test_instruction_name_and_version_mapped(self, mixin):
        attrs = self._llm_attrs(**{
            f"{SPAN_PREFIX}.instruction.name": "greet",
            f"{SPAN_PREFIX}.instruction.version": 3,
        })
        result = mixin._map_generation_attributes(attrs)
        assert result["langfuse.observation.prompt.name"] == "greet"
        assert result["langfuse.observation.prompt.version"] == 3

    def test_instruction_name_only(self, mixin):
        attrs = self._llm_attrs(**{f"{SPAN_PREFIX}.instruction.name": "greet"})
        result = mixin._map_generation_attributes(attrs)
        assert result["langfuse.observation.prompt.name"] == "greet"
        assert "langfuse.observation.prompt.version" not in result

    def test_instruction_version_zero(self, mixin):
        attrs = self._llm_attrs(**{f"{SPAN_PREFIX}.instruction.version": 0})
        result = mixin._map_generation_attributes(attrs)
        assert result["langfuse.observation.prompt.version"] == 0

    def test_generation_metadata_excludes_specific_keys(self, mixin):
        attrs = self._llm_attrs(**{
            f"{SPAN_PREFIX}.custom_key": "val",
            f"{SPAN_PREFIX}.llm_request": "{}",
            f"{SPAN_PREFIX}.llm_response": "{}",
            f"{SPAN_PREFIX}.prompt.name": "should_exclude",
            f"{SPAN_PREFIX}.prompt.version": "should_exclude",
            f"{SPAN_PREFIX}.prompt.labels": "should_exclude",
        })
        result = mixin._map_generation_attributes(attrs)
        md = json.loads(result["langfuse.observation.metadata"])
        assert "custom_key" in md
        assert "llm_request" not in md
        assert "llm_response" not in md
        assert "prompt.name" not in md
        assert "prompt.version" not in md
        assert "prompt.labels" not in md

    def test_no_metadata_when_no_trpc_attrs(self, mixin):
        attrs = {"gen_ai.usage.input_tokens": "5"}
        result = mixin._map_generation_attributes(attrs)
        assert "langfuse.observation.metadata" not in result


# ---------------------------------------------------------------------------
# _map_tool_observation_attributes
# ---------------------------------------------------------------------------
class TestMapToolObservationAttributes:
    """Tests for _map_tool_observation_attributes (new format)."""

    def test_basic_tool_mapping(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.tool_call_args": '{"a": 1}',
            f"{SPAN_PREFIX}.tool_response": '{"r": 2}',
            "gen_ai.tool.name": "search",
            "gen_ai.tool.description": "Search the web",
            "gen_ai.tool.call.id": "call_123",
        }
        result = mixin._map_tool_observation_attributes(attrs)
        assert result["langfuse.observation.type"] == "span"
        assert result["langfuse.observation.input"] == '{"a": 1}'
        assert result["langfuse.observation.output"] == '{"r": 2}'
        md = json.loads(result["langfuse.observation.metadata"])
        assert md["tool_name"] == "search"
        assert md["tool_description"] == "Search the web"
        assert md["tool_call_id"] == "call_123"

    def test_state_in_tool_metadata(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.state.begin": "sb",
            f"{SPAN_PREFIX}.state.end": "se",
        }
        result = mixin._map_tool_observation_attributes(attrs)
        md = json.loads(result["langfuse.observation.metadata"])
        assert md["state_begin"] == "sb"
        assert md["state_end"] == "se"

    def test_tool_metadata_excludes_mapped_fields(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.tool_call_args": "args",
            f"{SPAN_PREFIX}.tool_response": "resp",
            f"{SPAN_PREFIX}.state.begin": "sb",
            f"{SPAN_PREFIX}.state.end": "se",
            f"{SPAN_PREFIX}.extra_field": "extra",
        }
        result = mixin._map_tool_observation_attributes(attrs)
        md = json.loads(result["langfuse.observation.metadata"])
        assert "tool_call_args" not in md
        assert "tool_response" not in md
        assert "state.begin" not in md
        assert "state.end" not in md
        assert md["extra_field"] == "extra"

    def test_defaults_when_missing(self, mixin):
        result = mixin._map_tool_observation_attributes({})
        assert result["langfuse.observation.input"] == "unknown"
        assert result["langfuse.observation.output"] == "unknown"
        assert "langfuse.observation.metadata" not in result

    def test_no_metadata_when_empty(self, mixin):
        attrs = {f"{SPAN_PREFIX}.tool_call_args": "a"}
        result = mixin._map_tool_observation_attributes(attrs)
        assert "langfuse.observation.metadata" not in result


# ---------------------------------------------------------------------------
# _map_span_observation_attributes
# ---------------------------------------------------------------------------
class TestMapSpanObservationAttributes:
    """Tests for _map_span_observation_attributes (new format)."""

    def test_passthrough_with_type(self, mixin):
        attrs = {"key": "value", "other": 42}
        result = mixin._map_span_observation_attributes(attrs)
        assert result["langfuse.observation.type"] == "span"
        assert result["key"] == "value"
        assert result["other"] == 42


# ---------------------------------------------------------------------------
# Old-format attribute mapping (_map_attributes_to_old_langfuse)
# ---------------------------------------------------------------------------
class TestMapAttributesToOldLangfuse:
    """Tests for _map_attributes_to_old_langfuse routing."""

    def test_run_runner_old(self, mixin):
        attrs = {"gen_ai.operation.name": "run_runner", f"{SPAN_PREFIX}.runner.name": "r"}
        result = mixin._map_attributes_to_old_langfuse(attrs)
        assert result["langfuse.trace.name"] == "r"

    def test_run_runner_cancelled_old(self, mixin):
        attrs = {"gen_ai.operation.name": "run_runner_cancelled", f"{SPAN_PREFIX}.runner.name": "r"}
        result = mixin._map_attributes_to_old_langfuse(attrs)
        assert result["langfuse.trace.name"] == "r"

    def test_run_agent_old(self, mixin):
        attrs = {"gen_ai.operation.name": "run_agent", f"{SPAN_PREFIX}.agent.input": "hi"}
        result = mixin._map_attributes_to_old_langfuse(attrs)
        assert result["input.value"] == "hi"

    def test_call_llm_old(self, mixin):
        attrs = {
            "gen_ai.operation.name": "call_llm",
            f"{SPAN_PREFIX}.llm_request": "{}",
            f"{SPAN_PREFIX}.llm_response": "resp",
        }
        result = mixin._map_attributes_to_old_langfuse(attrs)
        assert "gen_ai.prompt" in result

    def test_execute_tool_old(self, mixin):
        attrs = {"gen_ai.operation.name": "execute_tool", f"{SPAN_PREFIX}.tool_call_args": "a"}
        result = mixin._map_attributes_to_old_langfuse(attrs)
        assert result["input.value"] == "a"

    def test_unknown_operation_old(self, mixin):
        attrs = {"gen_ai.operation.name": "custom", "k": "v"}
        result = mixin._map_attributes_to_old_langfuse(attrs)
        assert result["k"] == "v"


# ---------------------------------------------------------------------------
# _map_old_trace_level_attributes
# ---------------------------------------------------------------------------
class TestMapOldTraceLevelAttributes:
    """Tests for _map_old_trace_level_attributes."""

    def test_basic_old_trace(self, mixin):
        attrs = {f"{SPAN_PREFIX}.runner.name": "r"}
        result = mixin._map_old_trace_level_attributes(attrs)
        assert result["langfuse.trace.name"] == "r"

    def test_user_session_old_format(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.runner.name": "r",
            f"{SPAN_PREFIX}.runner.user_id": "u",
            f"{SPAN_PREFIX}.runner.session_id": "s",
        }
        result = mixin._map_old_trace_level_attributes(attrs)
        assert result["user.id"] == "u"
        assert result["session.id"] == "s"

    def test_input_output_old_format(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.runner.name": "r",
            f"{SPAN_PREFIX}.runner.input": "in",
            f"{SPAN_PREFIX}.runner.output": "out",
        }
        result = mixin._map_old_trace_level_attributes(attrs)
        assert result["input.value"] == "in"
        assert result["output.value"] == "out"

    def test_state_and_cancellation_old_metadata(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.runner.name": "r",
            f"{SPAN_PREFIX}.state.begin": "s0",
            f"{SPAN_PREFIX}.state.end": "s1",
            f"{SPAN_PREFIX}.state.partial": "sp",
            f"{SPAN_PREFIX}.cancellation.reason": "timeout",
            f"{SPAN_PREFIX}.cancellation.agent_name": "a",
            f"{SPAN_PREFIX}.cancellation.branch": "b",
        }
        result = mixin._map_old_trace_level_attributes(attrs)
        md = json.loads(result["langfuse.metadata"])
        assert md["state_begin"] == "s0"
        assert md["state_end"] == "s1"
        assert md["state_partial"] == "sp"
        assert md["cancellation_reason"] == "timeout"
        assert md["cancellation_agent_name"] == "a"
        assert md["cancellation_branch"] == "b"

    def test_no_metadata_key_when_no_runner_attrs(self, mixin):
        attrs = {f"{SPAN_PREFIX}.runner.name": "r"}
        result = mixin._map_old_trace_level_attributes(attrs)
        assert "langfuse.metadata" in result  # runner.name still generates metadata
        md = json.loads(result["langfuse.metadata"])
        assert md["name"] == "r"

    def test_no_user_id_session_id_old(self, mixin):
        attrs = {f"{SPAN_PREFIX}.runner.name": "r"}
        result = mixin._map_old_trace_level_attributes(attrs)
        assert "user.id" not in result
        assert "session.id" not in result

    def test_no_input_output_old(self, mixin):
        attrs = {f"{SPAN_PREFIX}.runner.name": "r"}
        result = mixin._map_old_trace_level_attributes(attrs)
        assert "input.value" not in result
        assert "output.value" not in result


# ---------------------------------------------------------------------------
# _map_old_agent_observation_attributes
# ---------------------------------------------------------------------------
class TestMapOldAgentObservationAttributes:
    def test_agent_old_format(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.agent.input": "q",
            f"{SPAN_PREFIX}.agent.output": "a",
        }
        result = mixin._map_old_agent_observation_attributes(attrs)
        assert result["input.value"] == "q"
        assert result["output.value"] == "a"

    def test_defaults_old_format(self, mixin):
        result = mixin._map_old_agent_observation_attributes({})
        assert result["input.value"] == ""
        assert result["output.value"] == ""


# ---------------------------------------------------------------------------
# _map_old_generation_attributes
# ---------------------------------------------------------------------------
class TestMapOldGenerationAttributes:
    """Tests for _map_old_generation_attributes."""

    def _llm_attrs(self, **overrides):
        base = {
            f"{SPAN_PREFIX}.llm_request": json.dumps({
                "config": {"temperature": 0.9, "max_tokens": 2000, "top_p": 0.95},
            }),
            f"{SPAN_PREFIX}.llm_response": "response_text",
            "gen_ai.usage.input_tokens": "10",
            "gen_ai.usage.output_tokens": "20",
            "gen_ai.usage.total_tokens": "30",
        }
        base.update(overrides)
        return base

    def test_basic_old_generation(self, mixin):
        result = mixin._map_old_generation_attributes(self._llm_attrs())
        assert result["gen_ai.request.temperature"] == 0.9
        assert result["gen_ai.request.max_tokens"] == 2000
        assert result["gen_ai.request.top_p"] == 0.95
        assert result["gen_ai.usage.input_tokens"] == "10"
        assert result["gen_ai.usage.output_tokens"] == "20"
        assert result["gen_ai.usage.total_tokens"] == "30"

    def test_prompt_completion_mapping(self, mixin):
        attrs = self._llm_attrs()
        result = mixin._map_old_generation_attributes(attrs)
        assert result["gen_ai.prompt"] == attrs[f"{SPAN_PREFIX}.llm_request"]
        assert result["gen_ai.completion"] == "response_text"

    def test_defaults_when_no_llm_request(self, mixin):
        result = mixin._map_old_generation_attributes({})
        assert result["gen_ai.prompt"] == "unknown"
        assert result["gen_ai.completion"] == "unknown"
        assert result["gen_ai.request.temperature"] == 0.7
        assert result["gen_ai.request.max_tokens"] == 1000
        assert result["gen_ai.request.top_p"] == 1.0

    def test_model_name_from_gen_ai_request_model(self, mixin):
        attrs = self._llm_attrs(**{"gen_ai.request.model": "gpt-4"})
        result = mixin._map_old_generation_attributes(attrs)
        assert result["gen_ai.request.model"] == "gpt-4"

    def test_model_name_from_llm_model_name(self, mixin):
        attrs = self._llm_attrs(**{"llm.model_name": "claude-3"})
        result = mixin._map_old_generation_attributes(attrs)
        assert result["llm.model_name"] == "claude-3"

    def test_model_name_from_model(self, mixin):
        attrs = self._llm_attrs(**{"model": "gemini"})
        result = mixin._map_old_generation_attributes(attrs)
        assert result["model"] == "gemini"

    def test_model_name_priority(self, mixin):
        attrs = self._llm_attrs(**{
            "gen_ai.request.model": "gpt-4",
            "llm.model_name": "claude-3",
            "model": "gemini",
        })
        result = mixin._map_old_generation_attributes(attrs)
        assert result["gen_ai.request.model"] == "gpt-4"
        assert "llm.model_name" not in result or result.get("llm.model_name") == "claude-3"

    def test_old_generation_metadata(self, mixin):
        attrs = self._llm_attrs(**{f"{SPAN_PREFIX}.custom_attr": "val"})
        result = mixin._map_old_generation_attributes(attrs)
        md = json.loads(result["langfuse.metadata"])
        assert "custom_attr" in md
        assert "llm_request" not in md
        assert "llm_response" not in md

    def test_no_metadata_when_no_trpc_attrs(self, mixin):
        attrs = {"gen_ai.usage.input_tokens": "5"}
        result = mixin._map_old_generation_attributes(attrs)
        assert "langfuse.metadata" not in result


# ---------------------------------------------------------------------------
# _map_old_tool_observation_attributes
# ---------------------------------------------------------------------------
class TestMapOldToolObservationAttributes:
    """Tests for _map_old_tool_observation_attributes."""

    def test_basic_old_tool(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.tool_call_args": "args",
            f"{SPAN_PREFIX}.tool_response": "resp",
            "gen_ai.tool.name": "calculator",
            "gen_ai.tool.description": "Does math",
            "gen_ai.tool.call.id": "id_1",
        }
        result = mixin._map_old_tool_observation_attributes(attrs)
        assert result["input.value"] == "args"
        assert result["output.value"] == "resp"
        md = json.loads(result["langfuse.metadata"])
        assert md["tool_name"] == "calculator"
        assert md["tool_description"] == "Does math"
        assert md["tool_call_id"] == "id_1"

    def test_state_in_old_tool_metadata(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.state.begin": "sb",
            f"{SPAN_PREFIX}.state.end": "se",
        }
        result = mixin._map_old_tool_observation_attributes(attrs)
        md = json.loads(result["langfuse.metadata"])
        assert md["state_begin"] == "sb"
        assert md["state_end"] == "se"

    def test_old_tool_excludes_mapped_fields(self, mixin):
        attrs = {
            f"{SPAN_PREFIX}.tool_call_args": "a",
            f"{SPAN_PREFIX}.tool_response": "r",
            f"{SPAN_PREFIX}.state.begin": "sb",
            f"{SPAN_PREFIX}.state.end": "se",
            f"{SPAN_PREFIX}.other": "v",
        }
        result = mixin._map_old_tool_observation_attributes(attrs)
        md = json.loads(result["langfuse.metadata"])
        assert "tool_call_args" not in md
        assert "tool_response" not in md
        assert "state.begin" not in md
        assert "state.end" not in md
        assert md["other"] == "v"

    def test_defaults_when_missing_old(self, mixin):
        result = mixin._map_old_tool_observation_attributes({})
        assert result["input.value"] == "unknown"
        assert result["output.value"] == "unknown"
        assert "langfuse.metadata" not in result


# ---------------------------------------------------------------------------
# _map_old_span_observation_attributes
# ---------------------------------------------------------------------------
class TestMapOldSpanObservationAttributes:
    def test_passthrough_old(self, mixin):
        attrs = {"a": 1, "b": 2}
        result = mixin._map_old_span_observation_attributes(attrs)
        assert result == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# _LangfuseSpanProcessor
# ---------------------------------------------------------------------------
class TestLangfuseSpanProcessor:
    """Tests for _LangfuseSpanProcessor.on_end."""

    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.SimpleSpanProcessor.on_end")
    def test_on_end_delegates_when_not_skipped(self, mock_super_on_end):
        exporter = MagicMock()
        processor = _LangfuseSpanProcessor(exporter)
        span = _make_span(name="my_op", attributes={}, scope_name="my.app")
        with patch.object(ReadableSpan, "to_json", return_value="{}"):
            processor.on_end(span)
        mock_super_on_end.assert_called_once()

    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.SimpleSpanProcessor.on_end")
    def test_on_end_skips_a2a_span(self, mock_super_on_end):
        exporter = MagicMock()
        processor = _LangfuseSpanProcessor(exporter)
        span = _make_span(name="op", scope_name="a2a-python-sdk")
        processor.on_end(span)
        mock_super_on_end.assert_not_called()


# ---------------------------------------------------------------------------
# _LangfuseBatchSpanProcessor
# ---------------------------------------------------------------------------
class TestLangfuseBatchSpanProcessor:
    """Tests for _LangfuseBatchSpanProcessor.on_end."""

    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.BatchSpanProcessor.on_end")
    def test_on_end_delegates_when_not_skipped(self, mock_super_on_end):
        exporter = MagicMock()
        processor = _LangfuseBatchSpanProcessor(exporter)
        span = _make_span(name="my_op", attributes={}, scope_name="my.app")
        with patch.object(ReadableSpan, "to_json", return_value="{}"):
            processor.on_end(span)
        mock_super_on_end.assert_called_once()

    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.BatchSpanProcessor.on_end")
    def test_on_end_skips_http_span(self, mock_super_on_end):
        exporter = MagicMock()
        processor = _LangfuseBatchSpanProcessor(exporter)
        span = _make_span(name="GET /api/test", scope_name="custom")
        processor.on_end(span)
        mock_super_on_end.assert_not_called()


# ---------------------------------------------------------------------------
# _LangfuseOTLPExporter
# ---------------------------------------------------------------------------
class TestLangfuseOTLPExporter:
    """Tests for _LangfuseOTLPExporter."""

    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.OTLPSpanExporter.__init__", return_value=None)
    def test_init_passes_params(self, mock_init):
        _LangfuseOTLPExporter(endpoint="http://ep", headers={"Authorization": "Basic x"})
        mock_init.assert_called_once_with(endpoint="http://ep", headers={"Authorization": "Basic x"})

    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.OTLPSpanExporter.export")
    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.OTLPSpanExporter.__init__", return_value=None)
    def test_export_delegates_to_super(self, mock_init, mock_export):
        from opentelemetry.sdk.trace.export import SpanExportResult
        mock_export.return_value = SpanExportResult.SUCCESS
        exporter = _LangfuseOTLPExporter(endpoint="http://ep", headers={})
        span_mock = MagicMock()
        span_mock.to_json.return_value = "{}"
        result = exporter.export([span_mock])
        assert result == SpanExportResult.SUCCESS
        mock_export.assert_called_once_with([span_mock])


# ---------------------------------------------------------------------------
# setup()
# ---------------------------------------------------------------------------
class TestSetup:
    """Tests for the setup() function."""

    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.trace.set_tracer_provider")
    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry._LangfuseOTLPExporter")
    def test_setup_with_full_config(self, mock_exporter_cls, mock_set_tp):
        cfg = LangfuseConfig(public_key="pk", secret_key="sk", host="https://h.com/")
        provider = setup(cfg)
        assert provider is not None
        mock_set_tp.assert_called_once()
        expected_endpoint = "https://h.com/api/public/otel/v1/traces"
        mock_exporter_cls.assert_called_once()
        call_kwargs = mock_exporter_cls.call_args[1]
        assert call_kwargs["endpoint"] == expected_endpoint
        auth_str = base64.b64encode(b"pk:sk").decode()
        assert call_kwargs["headers"]["Authorization"] == f"Basic {auth_str}"

    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.trace.set_tracer_provider")
    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry._LangfuseOTLPExporter")
    def test_setup_reads_env_vars(self, mock_exporter_cls, mock_set_tp):
        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "env_pk",
            "LANGFUSE_SECRET_KEY": "env_sk",
            "LANGFUSE_HOST": "https://env.host.com",
        }):
            provider = setup()
        assert provider is not None
        mock_set_tp.assert_called_once()

    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.trace.set_tracer_provider")
    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry._LangfuseOTLPExporter")
    def test_setup_env_vars_override_none_config(self, mock_exporter_cls, mock_set_tp):
        cfg = LangfuseConfig(public_key=None, secret_key=None, host=None)
        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "pk2",
            "LANGFUSE_SECRET_KEY": "sk2",
            "LANGFUSE_HOST": "https://h2.com",
        }):
            provider = setup(cfg)
        assert provider is not None

    def test_setup_raises_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
            os.environ.pop("LANGFUSE_SECRET_KEY", None)
            os.environ.pop("LANGFUSE_HOST", None)
            with pytest.raises(ValueError, match="Missing required Langfuse credentials"):
                setup(LangfuseConfig())

    def test_setup_raises_without_host(self):
        with pytest.raises(ValueError, match="Missing required Langfuse credentials"):
            setup(LangfuseConfig(public_key="pk", secret_key="sk", host=None))

    def test_setup_raises_without_public_key(self):
        with pytest.raises(ValueError, match="Missing required Langfuse credentials"):
            setup(LangfuseConfig(public_key=None, secret_key="sk", host="https://h.com"))

    def test_setup_raises_without_secret_key(self):
        with pytest.raises(ValueError, match="Missing required Langfuse credentials"):
            setup(LangfuseConfig(public_key="pk", secret_key=None, host="https://h.com"))

    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.trace.set_tracer_provider")
    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry._LangfuseOTLPExporter")
    def test_setup_batch_export_default(self, mock_exporter_cls, mock_set_tp):
        cfg = LangfuseConfig(public_key="pk", secret_key="sk", host="https://h.com", batch_export=True)
        setup(cfg)
        assert otel_module._langfuse_config is cfg

    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.trace.set_tracer_provider")
    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry._LangfuseOTLPExporter")
    def test_setup_simple_export(self, mock_exporter_cls, mock_set_tp):
        cfg = LangfuseConfig(public_key="pk", secret_key="sk", host="https://h.com", batch_export=False)
        setup(cfg)
        assert otel_module._langfuse_config is cfg

    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.trace.set_tracer_provider")
    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry._LangfuseOTLPExporter")
    def test_setup_host_rstrip(self, mock_exporter_cls, mock_set_tp):
        cfg = LangfuseConfig(public_key="pk", secret_key="sk", host="https://h.com///")
        setup(cfg)
        call_kwargs = mock_exporter_cls.call_args[1]
        assert call_kwargs["endpoint"] == "https://h.com/api/public/otel/v1/traces"

    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.trace.set_tracer_provider")
    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry._LangfuseOTLPExporter")
    def test_setup_sets_global_config(self, mock_exporter_cls, mock_set_tp):
        cfg = LangfuseConfig(public_key="pk", secret_key="sk", host="https://h.com")
        setup(cfg)
        assert otel_module._langfuse_config is cfg

    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry.trace.set_tracer_provider")
    @patch("trpc_agent_sdk.server.langfuse.tracing.opentelemetry._LangfuseOTLPExporter")
    def test_setup_none_config_creates_default(self, mock_exporter_cls, mock_set_tp):
        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "pk",
            "LANGFUSE_SECRET_KEY": "sk",
            "LANGFUSE_HOST": "https://h.com",
        }):
            provider = setup(None)
        assert provider is not None
        assert otel_module._langfuse_config is not None
