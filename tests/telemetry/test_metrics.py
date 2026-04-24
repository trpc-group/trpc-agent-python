# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for :mod:`trpc_agent_sdk.telemetry._metrics`.

Uses an ``InMemoryMetricReader`` bound to a private ``MeterProvider`` so the
tests can inspect the OTel data points the ``report_*`` functions emit, without
touching the global meter provider.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from typing import Dict
from typing import Mapping
from typing import Optional

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from trpc_agent_sdk.telemetry import _metrics as tmetrics


def _make_ctx(
    *,
    app_name: str = "demo",
    user_id: str = "alice",
    agent_name: str = "asst",
    agent_model: Any = None,
) -> SimpleNamespace:
    """Build a duck-typed ``InvocationContext`` stub.

    The ``report_*`` functions only read ``app_name``, ``user_id``,
    ``agent_name``, and ``agent`` from the context, so a ``SimpleNamespace`` is
    sufficient and avoids having to construct a real session/agent.
    """
    agent = SimpleNamespace(model=agent_model)
    return SimpleNamespace(
        app_name=app_name,
        user_id=user_id,
        agent_name=agent_name,
        agent=agent,
    )


class _StubTool:

    def __init__(self, name: str):
        self.name = name


class _StubLlmRequest:

    def __init__(self, model: str):
        self.model = model


class _StubUsage:

    def __init__(self, prompt: int, total: int):
        self.prompt_token_count = prompt
        self.total_token_count = total


class _StubLlmResponse:

    def __init__(
        self,
        *,
        model: str = "",
        error_code: str = "",
        usage: Optional[_StubUsage] = None,
    ):
        self.model = model
        self.error_code = error_code
        self.usage_metadata = usage


@pytest.fixture()
def reader_provider(monkeypatch):
    """Install an ``InMemoryMetricReader`` on a private ``MeterProvider``.

    Rebinds the module-level instruments in :mod:`trpc_agent_sdk.telemetry._metrics`
    to the test meter so we can introspect emissions without global state.
    """
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")

    originals = {
        "_request_cnt": tmetrics._request_cnt,
        "_operation_duration": tmetrics._operation_duration,
        "_time_to_first_token": tmetrics._time_to_first_token,
        "_usage_input_tokens": tmetrics._usage_input_tokens,
        "_usage_output_tokens": tmetrics._usage_output_tokens,
    }
    monkeypatch.setattr(
        tmetrics,
        "_request_cnt",
        meter.create_counter("gen_ai.request_cnt"),
    )
    monkeypatch.setattr(
        tmetrics,
        "_operation_duration",
        meter.create_histogram("gen_ai.client.operation.duration"),
    )
    monkeypatch.setattr(
        tmetrics,
        "_time_to_first_token",
        meter.create_histogram("gen_ai.server.time_to_first_token"),
    )
    monkeypatch.setattr(
        tmetrics,
        "_usage_input_tokens",
        meter.create_histogram("gen_ai.usage.input_tokens"),
    )
    monkeypatch.setattr(
        tmetrics,
        "_usage_output_tokens",
        meter.create_histogram("gen_ai.usage.output_tokens"),
    )

    yield reader, provider

    for name, inst in originals.items():
        monkeypatch.setattr(tmetrics, name, inst)
    provider.shutdown()


def _collect(reader: InMemoryMetricReader) -> Dict[str, list]:
    """Collect and index data points by metric name."""
    data = reader.get_metrics_data()
    out: Dict[str, list] = {}
    if data is None:
        return out
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                for dp in getattr(metric.data, "data_points", []) or []:
                    out.setdefault(metric.name, []).append(dp)
    return out


def _attrs(dp) -> Mapping[str, Any]:
    return dict(dp.attributes or {})


class TestInferSystem:
    """Vendor inference from model name."""

    @pytest.mark.parametrize(
        "model,expected",
        [
            ("gpt-4", "openai"),
            ("gpt-4o-mini", "openai"),
            ("o1-preview", "openai"),
            ("text-embedding-3-large", "openai"),
            ("claude-3-5-sonnet", "anthropic"),
            ("CLAUDE-opus", "anthropic"),
            ("gemini-2.0-flash", "gcp.gemini"),
            ("hunyuan-pro", "hunyuan"),
            ("taiji-v1", "taiji"),
            ("", ""),
            ("unknown-model-x", ""),
        ],
    )
    def test_known_and_unknown(self, model: str, expected: str):
        assert tmetrics._infer_system(model) == expected


class TestAgentModelName:
    """Best-effort extraction of the agent's model name."""

    def test_string_model(self):
        agent = SimpleNamespace(model="claude-3-haiku")
        assert tmetrics._agent_model_name(agent) == "claude-3-haiku"

    def test_model_with_name_property(self):
        agent = SimpleNamespace(model=SimpleNamespace(name="gpt-4"))
        assert tmetrics._agent_model_name(agent) == "gpt-4"

    def test_missing_model_attribute(self):
        agent = SimpleNamespace()
        assert tmetrics._agent_model_name(agent) == ""

    def test_model_is_none(self):
        agent = SimpleNamespace(model=None)
        assert tmetrics._agent_model_name(agent) == ""

    def test_model_is_callable(self):
        """Callable factories (lazy agents) are not statically reachable."""
        agent = SimpleNamespace(model=lambda *_: None)
        assert tmetrics._agent_model_name(agent) == ""


class TestMergeExtras:

    def test_no_extras(self):
        base = {"a": 1}
        assert tmetrics._merge_extras(base, None) is base

    def test_extras_override_base(self):
        base = {"a": 1, "b": 2}
        out = tmetrics._merge_extras(base, {"b": 3, "c": 4})
        assert out == {"a": 1, "b": 3, "c": 4}
        assert base == {"a": 1, "b": 2}, "base must not be mutated"

    def test_none_values_are_skipped(self):
        out = tmetrics._merge_extras({"a": 1}, {"a": None, "b": None, "c": 7})
        assert out == {"a": 1, "c": 7}


class TestReportCallLlm:

    def test_emits_request_cnt_duration_and_ttft(self, reader_provider):
        reader, _ = reader_provider
        ctx = _make_ctx()
        tmetrics.report_call_llm(
            ctx,
            _StubLlmRequest("gpt-4"),
            _StubLlmResponse(model="gpt-4-0613"),
            duration_s=1.25,
            ttft_s=0.2,
            is_stream=True,
        )
        metrics = _collect(reader)

        assert "gen_ai.request_cnt" in metrics
        assert "gen_ai.client.operation.duration" in metrics
        assert "gen_ai.server.time_to_first_token" in metrics
        assert "gen_ai.usage.input_tokens" not in metrics
        assert "gen_ai.usage.output_tokens" not in metrics

        cnt_dp = metrics["gen_ai.request_cnt"][0]
        assert cnt_dp.value == 1
        attrs = _attrs(cnt_dp)
        assert attrs["gen_ai.operation.name"] == "chat"
        assert attrs["gen_ai.system"] == "openai"
        assert attrs["gen_ai.app.name"] == "demo"
        assert attrs["gen_ai.user.id"] == "alice"
        assert attrs["gen_ai.agent.id"] == "asst"
        assert attrs["gen_ai.agent.name"] == "asst"
        assert attrs["gen_ai.request.model"] == "gpt-4"
        assert attrs["gen_ai.response.model"] == "gpt-4-0613"
        assert attrs["gen_ai.is_stream"] is True
        assert attrs["error.type"] == ""
        assert attrs["gen_ai.response.error_code"] == ""

        dur_dp = metrics["gen_ai.client.operation.duration"][0]
        assert dur_dp.sum == pytest.approx(1.25)
        ttft_dp = metrics["gen_ai.server.time_to_first_token"][0]
        assert ttft_dp.sum == pytest.approx(0.2)

    def test_usage_tokens_emitted_when_usage_metadata_present(self, reader_provider):
        reader, _ = reader_provider
        ctx = _make_ctx()
        tmetrics.report_call_llm(
            ctx,
            _StubLlmRequest("claude-3-5-sonnet"),
            _StubLlmResponse(
                model="claude-3-5-sonnet",
                usage=_StubUsage(prompt=120, total=170),
            ),
            duration_s=2.0,
            ttft_s=0.3,
            is_stream=False,
        )
        metrics = _collect(reader)

        inp = metrics["gen_ai.usage.input_tokens"][0]
        out = metrics["gen_ai.usage.output_tokens"][0]
        assert inp.sum == 120
        assert out.sum == 50

    def test_usage_tokens_skipped_when_missing(self, reader_provider):
        reader, _ = reader_provider
        ctx = _make_ctx()
        tmetrics.report_call_llm(
            ctx,
            _StubLlmRequest("gpt-4"),
            None,
            duration_s=1.0,
            ttft_s=0.1,
            is_stream=False,
        )
        metrics = _collect(reader)
        assert "gen_ai.usage.input_tokens" not in metrics
        assert "gen_ai.usage.output_tokens" not in metrics

    def test_usage_zero_tokens_skipped(self, reader_provider):
        reader, _ = reader_provider
        ctx = _make_ctx()
        tmetrics.report_call_llm(
            ctx,
            _StubLlmRequest("gpt-4"),
            _StubLlmResponse(model="gpt-4", usage=_StubUsage(prompt=0, total=0)),
            duration_s=1.0,
            ttft_s=0.1,
            is_stream=False,
        )
        metrics = _collect(reader)
        assert "gen_ai.usage.input_tokens" not in metrics
        assert "gen_ai.usage.output_tokens" not in metrics

    def test_error_type_and_response_code_propagate(self, reader_provider):
        reader, _ = reader_provider
        ctx = _make_ctx()
        tmetrics.report_call_llm(
            ctx,
            _StubLlmRequest("gpt-4"),
            _StubLlmResponse(model="gpt-4", error_code="429"),
            duration_s=0.1,
            ttft_s=0.1,
            is_stream=False,
            error_type="rate_limit",
        )
        cnt_dp = _collect(reader)["gen_ai.request_cnt"][0]
        attrs = _attrs(cnt_dp)
        assert attrs["error.type"] == "rate_limit"
        assert attrs["gen_ai.response.error_code"] == "429"

    def test_extra_attributes_override_inferred_system(self, reader_provider):
        reader, _ = reader_provider
        ctx = _make_ctx()
        tmetrics.report_call_llm(
            ctx,
            _StubLlmRequest("some-custom-model"),
            _StubLlmResponse(model="some-custom-model"),
            duration_s=0.1,
            ttft_s=0.1,
            is_stream=False,
            extra_attributes={
                "gen_ai.system": "openai",
                "user_ext1": "abc"
            },
        )
        attrs = _attrs(_collect(reader)["gen_ai.request_cnt"][0])
        assert attrs["gen_ai.system"] == "openai"
        assert attrs["user_ext1"] == "abc"


class TestReportExecuteTool:

    def test_emits_request_cnt_and_duration(self, reader_provider):
        reader, _ = reader_provider
        ctx = _make_ctx(agent_model="hunyuan-pro")
        tmetrics.report_execute_tool(ctx, _StubTool("search"), duration_s=0.5)
        metrics = _collect(reader)

        assert "gen_ai.request_cnt" in metrics
        assert "gen_ai.client.operation.duration" in metrics
        assert "gen_ai.server.time_to_first_token" not in metrics
        assert "gen_ai.usage.input_tokens" not in metrics

        attrs = _attrs(metrics["gen_ai.request_cnt"][0])
        assert attrs["gen_ai.operation.name"] == "execute_tool"
        assert attrs["gen_ai.tool.name"] == "search"
        assert attrs["gen_ai.system"] == "hunyuan"
        assert attrs["gen_ai.app.name"] == "demo"

    def test_system_empty_when_agent_has_no_model(self, reader_provider):
        reader, _ = reader_provider
        ctx = _make_ctx(agent_model=None)
        tmetrics.report_execute_tool(ctx, _StubTool("search"), duration_s=0.5)
        attrs = _attrs(_collect(reader)["gen_ai.request_cnt"][0])
        assert attrs["gen_ai.system"] == ""

    def test_extra_attrs_can_supply_system(self, reader_provider):
        reader, _ = reader_provider
        ctx = _make_ctx(agent_model=None)
        tmetrics.report_execute_tool(
            ctx,
            _StubTool("search"),
            duration_s=0.5,
            extra_attributes={"gen_ai.system": "openai"},
        )
        attrs = _attrs(_collect(reader)["gen_ai.request_cnt"][0])
        assert attrs["gen_ai.system"] == "openai"

    def test_error_type_propagates(self, reader_provider):
        reader, _ = reader_provider
        ctx = _make_ctx(agent_model="claude-3-haiku")
        tmetrics.report_execute_tool(ctx, _StubTool("search"), duration_s=0.5, error_type="timeout")
        attrs = _attrs(_collect(reader)["gen_ai.request_cnt"][0])
        assert attrs["error.type"] == "timeout"
        assert attrs["gen_ai.system"] == "anthropic"


class TestReportInvokeAgent:

    def test_emits_all_five_instruments(self, reader_provider):
        reader, _ = reader_provider
        ctx = _make_ctx(agent_model="gpt-4")
        tmetrics.report_invoke_agent(
            ctx,
            duration_s=3.0,
            ttft_s=0.4,
            input_tokens=200,
            output_tokens=50,
            is_stream=True,
        )
        metrics = _collect(reader)

        assert metrics["gen_ai.request_cnt"][0].value == 1
        assert metrics["gen_ai.client.operation.duration"][0].sum == pytest.approx(3.0)
        assert metrics["gen_ai.server.time_to_first_token"][0].sum == pytest.approx(0.4)
        assert metrics["gen_ai.usage.input_tokens"][0].sum == 200
        assert metrics["gen_ai.usage.output_tokens"][0].sum == 50

        attrs = _attrs(metrics["gen_ai.request_cnt"][0])
        assert attrs["gen_ai.operation.name"] == "invoke_agent"
        assert attrs["gen_ai.system"] == "openai"
        assert attrs["gen_ai.is_stream"] is True
        assert attrs["gen_ai.agent.name"] == "asst"

    def test_zero_tokens_are_skipped(self, reader_provider):
        reader, _ = reader_provider
        ctx = _make_ctx(agent_model="gpt-4")
        tmetrics.report_invoke_agent(
            ctx,
            duration_s=1.0,
            ttft_s=0.1,
            input_tokens=0,
            output_tokens=0,
            is_stream=False,
        )
        metrics = _collect(reader)
        assert "gen_ai.usage.input_tokens" not in metrics
        assert "gen_ai.usage.output_tokens" not in metrics

    def test_partial_tokens_are_independently_skipped(self, reader_provider):
        reader, _ = reader_provider
        ctx = _make_ctx(agent_model="gpt-4")
        tmetrics.report_invoke_agent(
            ctx,
            duration_s=1.0,
            ttft_s=0.1,
            input_tokens=120,
            output_tokens=0,
            is_stream=False,
        )
        metrics = _collect(reader)
        assert metrics["gen_ai.usage.input_tokens"][0].sum == 120
        assert "gen_ai.usage.output_tokens" not in metrics

    def test_error_type_propagates(self, reader_provider):
        reader, _ = reader_provider
        ctx = _make_ctx(agent_model="gpt-4")
        tmetrics.report_invoke_agent(
            ctx,
            duration_s=0.1,
            ttft_s=0.1,
            input_tokens=0,
            output_tokens=0,
            is_stream=True,
            error_type="cancelled",
        )
        attrs = _attrs(_collect(reader)["gen_ai.request_cnt"][0])
        assert attrs["error.type"] == "cancelled"


class TestOperationRouting:

    def test_three_operations_create_three_separate_streams(self, reader_provider):
        """Same counter, different attrs -> three independent time series."""
        reader, _ = reader_provider
        ctx = _make_ctx(agent_model="gpt-4")

        tmetrics.report_invoke_agent(
            ctx,
            duration_s=1.0,
            ttft_s=0.1,
            input_tokens=0,
            output_tokens=0,
            is_stream=True,
        )
        tmetrics.report_call_llm(
            ctx,
            _StubLlmRequest("gpt-4"),
            _StubLlmResponse(model="gpt-4"),
            duration_s=0.5,
            ttft_s=0.1,
            is_stream=True,
        )
        tmetrics.report_execute_tool(ctx, _StubTool("calc"), duration_s=0.2)

        request_cnt_dps = _collect(reader)["gen_ai.request_cnt"]
        ops = sorted(_attrs(dp)["gen_ai.operation.name"] for dp in request_cnt_dps)
        assert ops == ["chat", "execute_tool", "invoke_agent"]
        for dp in request_cnt_dps:
            assert dp.value == 1

    def test_repeated_same_operation_aggregates(self, reader_provider):
        """Identical attrs -> single time series with summed value."""
        reader, _ = reader_provider
        ctx = _make_ctx(agent_model="gpt-4")
        for _ in range(3):
            tmetrics.report_call_llm(
                ctx,
                _StubLlmRequest("gpt-4"),
                _StubLlmResponse(model="gpt-4"),
                duration_s=0.1,
                ttft_s=0.05,
                is_stream=True,
            )
        dps = _collect(reader)["gen_ai.request_cnt"]
        assert len(dps) == 1
        assert dps[0].value == 3
