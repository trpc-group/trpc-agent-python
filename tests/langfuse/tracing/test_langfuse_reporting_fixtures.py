# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Langfuse reporting regression tests tied to the active pytest interpreter.

Expected behaviour (same test file, different venv):
- ``./venv/bin/pytest tests/langfuse/tracing/test_langfuse_reporting_fixtures.py`` → PASS
  (dev env: no broken site-packages copy, or fixed source).
- ``./examples/quickstart/venv/bin/pytest ...`` → FAIL
  (quickstart venv ships an older ``trpc_agent_sdk`` in site-packages with detached spans).

The probe reads span-creation code from **site-packages when present**, matching
``run_agent.py`` under ``examples/quickstart/``. Repo-root ``sys.path`` is ignored
for that detection so pytest at repo root still exercises the installed wheel.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable
from typing import Iterable
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import trpc_agent_sdk.server.langfuse.tracing.opentelemetry as otel_module
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.server.langfuse.tracing.opentelemetry import LangfuseConfig, _LangfuseMixin
from trpc_agent_sdk.telemetry._trace import trace_agent
from trpc_agent_sdk.telemetry._trace import trace_runner
from trpc_agent_sdk.telemetry._trace import tracer
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

SPAN_PREFIX = "trpc.python.agent"

SYSTEM_INSTRUCTION = (
    "You are an agent who's name is [assistant].\n\n"
    "You are a helpful assistant for query weather."
)
TOOLS = [
    {
        "function_declarations": [
            {
                "description": "get weather information for the specified city",
                "name": "get_weather_report",
                "parameters": {
                    "properties": {"city": {"type": "STRING"}},
                    "type": "OBJECT",
                },
            }
        ]
    }
]
LLM_REQUEST = {
    "model": "glm-5.0-w4afp8",
    "config": {
        "system_instruction": SYSTEM_INSTRUCTION,
        "tools": TOOLS,
    },
    "contents": [
        {
            "parts": [{"text": "What's the weather like today?"}],
            "role": "user",
        }
    ],
}
LLM_RESPONSE = {
    "content": {
        "parts": [
            {"text": "assistant reply", "thought": False},
        ],
        "role": "model",
    },
    "partial": False,
    "usage_metadata": {
        "candidates_token_count": 107,
        "prompt_token_count": 185,
        "total_token_count": 292,
    },
}


def _iter_site_packages() -> list[Path]:
    paths: list[Path] = []
    prefix = Path(sys.prefix)
    for lib_dir in ("lib64", "lib"):
        base = prefix / lib_dir
        if not base.is_dir():
            continue
        for child in sorted(base.glob("python*/site-packages")):
            if child.is_dir():
                paths.append(child)
    return paths


def _resolve_sdk_file(relative: str) -> Path:
    """Resolve a SDK file, preferring site-packages over repo-source import."""
    for site in _iter_site_packages():
        candidate = site / "trpc_agent_sdk" / relative
        if candidate.is_file():
            return candidate
    module_path = "trpc_agent_sdk." + relative.replace("/", ".").removesuffix(".py")
    module = __import__(module_path, fromlist=["_"])
    return Path(module.__file__)


def _span_pattern_from_source(
    source: str,
    *,
    detached_needles: Iterable[str],
    current_needles: Iterable[str],
) -> str:
    """Return ``current`` or ``detached`` based on how a span is opened in source text."""
    detached_needles = tuple(detached_needles)
    current_needles = tuple(current_needles)
    has_current = any(needle in source for needle in current_needles)
    has_detached = any(needle in source for needle in detached_needles)
    if has_current and not has_detached:
        return "current"
    if has_detached and not has_current:
        return "detached"
    if has_current:
        return "current"
    if has_detached:
        return "detached"
    raise AssertionError(
        f"cannot detect span pattern (needles detached={detached_needles!r} "
        f"current={current_needles!r})"
    )


def _agent_run_span_pattern() -> str:
    source = _resolve_sdk_file("agents/_base_agent.py").read_text(encoding="utf-8")
    return _span_pattern_from_source(
        source,
        detached_needles=('span = tracer.start_span(f"agent_run',),
        current_needles=('with tracer.start_as_current_span(f"agent_run',),
    )


def _invocation_span_pattern() -> str:
    source = _resolve_sdk_file("runners.py").read_text(encoding="utf-8")
    return _span_pattern_from_source(
        source,
        detached_needles=('span = tracer.start_span("invocation")',),
        current_needles=(
            'with tracer.start_as_current_span("invocation")',
            'with tracer.start_as_current_span(f"invocation")',
        ),
    )


_EXPORTER: InMemorySpanExporter | None = None


@pytest.fixture(scope="session", autouse=True)
def _init_otel_tracer_once():
    """OpenTelemetry allows only one TracerProvider; share it across this module."""
    global _EXPORTER  # noqa: PLW0603
    _EXPORTER = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    trace.set_tracer_provider(provider)
    yield
    _EXPORTER = None


def _clear_finished_spans() -> None:
    assert _EXPORTER is not None
    _EXPORTER.clear()


def _run_with_span_pattern(pattern: str, span_name: str, callback: Callable[[], None]) -> None:
    if pattern == "current":
        with tracer.start_as_current_span(span_name):
            callback()
        return
    if pattern == "detached":
        span = tracer.start_span(span_name)
        try:
            callback()
        finally:
            span.end()
        return
    raise ValueError(f"unknown span pattern: {pattern}")


def _finished_span_attributes(name_substring: str) -> dict:
    assert _EXPORTER is not None
    spans = _EXPORTER.get_finished_spans()
    matched = [span for span in spans if name_substring in span.name]
    assert matched, (
        f"no finished span containing {name_substring!r}; "
        f"got span names: {[span.name for span in spans]}"
    )
    return dict(matched[0].attributes or {})


def _map_to_langfuse(raw_attributes: dict) -> dict:
    mixin = _LangfuseMixin()
    otel_module._langfuse_config = LangfuseConfig()
    return mixin._map_attributes_to_langfuse(raw_attributes)


def _make_invocation_context() -> MagicMock:
    ctx = MagicMock()
    ctx.agent.name = "assistant"
    ctx.user_content = Content(role="user", parts=[Part(text="What's the weather like today?")])
    ctx.override_messages = None
    ctx.session.id = "a252d252-4b55-4713-80e4-90abb177c433"
    ctx.session.user_id = "demo_user"
    ctx.invocation_id = "e-d5a9872c-80e3-43ea-b2a8-0091257a1616"
    return ctx


def probe_agent_run_langfuse_mapping() -> dict:
    _clear_finished_spans()
    ctx = _make_invocation_context()
    pattern = _agent_run_span_pattern()

    def _callback() -> None:
        trace_agent(
            invocation_context=ctx,
            agent_action="Could you please tell me the city you're interested in?",
            state_begin={"user_name": "demo_user"},
            state_end={"user_name": "demo_user"},
        )

    _run_with_span_pattern(pattern, "agent_run [assistant]", _callback)
    return _map_to_langfuse(_finished_span_attributes("agent_run"))


def probe_invocation_langfuse_mapping() -> dict:
    _clear_finished_spans()
    ctx = _make_invocation_context()
    pattern = _invocation_span_pattern()
    user_message = Content(role="user", parts=[Part(text="What's the weather like today?")])
    last_event = Event(
        content=Content(role="model", parts=[Part(text="Could you please tell me the city you're interested in?")]),
    )

    def _callback() -> None:
        trace_runner(
            app_name="weather_agent_demo",
            user_id="demo_user",
            session_id="a252d252-4b55-4713-80e4-90abb177c433",
            invocation_context=ctx,
            new_message=user_message,
            last_event=last_event,
            state_begin={"user_name": "demo_user"},
            state_end={"user_name": "demo_user"},
        )

    _run_with_span_pattern(pattern, "invocation", _callback)
    return _map_to_langfuse(_finished_span_attributes("invocation"))


def assert_valid_call_llm_langfuse_mapping(result: dict) -> None:
    assert result["langfuse.observation.type"] == "generation", result
    llm_input = json.loads(result["langfuse.observation.input"])
    config = llm_input.get("config", {})
    assert config.get("system_instruction"), result
    assert config.get("tools"), result
    model_params = json.loads(result["langfuse.observation.model.parameters"])
    assert model_params.get("system_instruction"), result
    assert model_params.get("tools"), result
    assert result["langfuse.observation.output"] != "unknown", result


def assert_valid_run_agent_langfuse_mapping(result: dict) -> None:
    assert result.get("langfuse.observation.type") == "span", result
    assert result.get("langfuse.observation.input") == "What's the weather like today?", result
    assert result.get("langfuse.observation.output"), result


def assert_valid_run_runner_langfuse_mapping(result: dict) -> None:
    assert result.get("langfuse.trace.name") == "[trpc-agent]: weather_agent_demo/assistant", result
    assert result.get("langfuse.user.id") == "demo_user", result
    assert result.get("langfuse.session.id") == "a252d252-4b55-4713-80e4-90abb177c433", result
    assert result.get("langfuse.observation.input") == "What's the weather like today?", result
    assert result.get("langfuse.observation.output"), result
    assert "langfuse.trace.metadata" in result, result


@pytest.fixture(autouse=True)
def _langfuse_config():
    original = otel_module._langfuse_config
    otel_module._langfuse_config = LangfuseConfig()
    yield
    otel_module._langfuse_config = original


@pytest.fixture
def mixin():
    return _LangfuseMixin()


class TestLangfuseReportingSpanContext:
    """End-to-end: telemetry must land on the span Langfuse exports (ok.txt vs error.txt)."""

    def test_trace_agent_reaches_agent_run_span(self):
        result = probe_agent_run_langfuse_mapping()
        assert_valid_run_agent_langfuse_mapping(result)

    def test_trace_runner_reaches_invocation_span(self):
        result = probe_invocation_langfuse_mapping()
        assert_valid_run_runner_langfuse_mapping(result)


class TestLangfuseReportingCallLlmMapping:
    """call_llm mapping must always include system prompt and tools (ok.txt generation)."""

    def test_call_llm_mapping_includes_system_instruction_and_tools(self, mixin):
        attrs = {
            "gen_ai.operation.name": "call_llm",
            f"{SPAN_PREFIX}.llm_request": json.dumps(LLM_REQUEST, ensure_ascii=False),
            f"{SPAN_PREFIX}.llm_response": json.dumps(LLM_RESPONSE, ensure_ascii=False),
            "gen_ai.usage.input_tokens": 185,
            "gen_ai.usage.output_tokens": 107,
            "gen_ai.request.model": "glm-5.0-w4afp8",
        }
        result = mixin._map_attributes_to_langfuse(attrs)
        assert_valid_call_llm_langfuse_mapping(result)
