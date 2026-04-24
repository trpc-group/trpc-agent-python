# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""OTel-native ``gen_ai.*`` metrics for the TRPC Agent framework.

Mirrors :mod:`trpc_agent_sdk.telemetry._trace`: module-level instruments plus
``report_*`` free functions. Backends fan out via the installed
``MeterProvider`` and route by ``gen_ai.operation.name``.
"""

from __future__ import annotations

from typing import Any
from typing import Mapping
from typing import Optional

from opentelemetry import metrics

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.tools import BaseTool

_meter = metrics.get_meter("trpc.python.agent")

_request_cnt = _meter.create_counter(
    name="gen_ai.request_cnt",
    description="Number of gen_ai operations.",
    unit="{request}",
)
_operation_duration = _meter.create_histogram(
    name="gen_ai.client.operation.duration",
    description="End-to-end wall-clock time of a gen_ai operation.",
    unit="s",
)
_time_to_first_token = _meter.create_histogram(
    name="gen_ai.server.time_to_first_token",
    description="Time to first output token / visible chunk.",
    unit="s",
)
_usage_input_tokens = _meter.create_histogram(
    name="gen_ai.usage.input_tokens",
    description="Prompt tokens consumed by a gen_ai operation.",
    unit="{token}",
)
_usage_output_tokens = _meter.create_histogram(
    name="gen_ai.usage.output_tokens",
    description="Completion tokens produced by a gen_ai operation.",
    unit="{token}",
)

# OTel GenAI semconv attribute keys.
_ATTR_OPERATION_NAME = "gen_ai.operation.name"
_ATTR_SYSTEM = "gen_ai.system"
_ATTR_APP_NAME = "gen_ai.app.name"
_ATTR_USER_ID = "gen_ai.user.id"
_ATTR_AGENT_ID = "gen_ai.agent.id"
_ATTR_AGENT_NAME = "gen_ai.agent.name"
_ATTR_REQUEST_MODEL = "gen_ai.request.model"
_ATTR_RESPONSE_MODEL = "gen_ai.response.model"
_ATTR_IS_STREAM = "gen_ai.is_stream"
_ATTR_TOOL_NAME = "gen_ai.tool.name"
_ATTR_ERROR_TYPE = "error.type"
_ATTR_RESPONSE_ERROR_CODE = "gen_ai.response.error_code"

_OP_CHAT = "chat"
_OP_EXECUTE_TOOL = "execute_tool"
_OP_INVOKE_AGENT = "invoke_agent"


def _merge_extras(
    base: dict[str, Any],
    extras: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    if not extras:
        return base
    out = dict(base)
    for k, v in extras.items():
        if v is None:
            continue
        out[k] = v
    return out


def _infer_system(model: str) -> str:
    """Map a model name to a ``gen_ai.system`` value; empty string if unknown."""
    if not model:
        return ""
    m = model.lower()
    if m.startswith(("gpt", "o1", "text-embedding")):
        return "openai"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gemini"):
        return "gcp.gemini"
    if m.startswith("hunyuan"):
        return "hunyuan"
    if m.startswith("taiji"):
        return "taiji"
    return ""


def _agent_model_name(agent: Any) -> str:
    """Best-effort model name from an agent; "" if not statically reachable."""
    model = getattr(agent, "model", None)
    if isinstance(model, str):
        return model
    name = getattr(model, "name", None)
    if isinstance(name, str):
        return name
    return ""


def report_call_llm(
    invocation_context: InvocationContext,
    llm_request: LlmRequest,
    llm_response: Optional[LlmResponse],
    *,
    duration_s: float,
    ttft_s: float,
    is_stream: bool,
    error_type: Optional[str] = None,
    extra_attributes: Optional[Mapping[str, Any]] = None,
) -> None:
    """Record one LLM call. Token histograms are skipped without ``usage_metadata``."""
    request_model = getattr(llm_request, "model", "") or ""
    response_model = ""
    response_error_code = ""
    if llm_response is not None:
        response_model = getattr(llm_response, "model", "") or request_model
        response_error_code = getattr(llm_response, "error_code", "") or ""

    attrs = {
        _ATTR_OPERATION_NAME: _OP_CHAT,
        _ATTR_SYSTEM: _infer_system(request_model),
        _ATTR_APP_NAME: invocation_context.app_name,
        _ATTR_USER_ID: invocation_context.user_id,
        _ATTR_AGENT_ID: invocation_context.agent_name,
        _ATTR_AGENT_NAME: invocation_context.agent_name,
        _ATTR_REQUEST_MODEL: request_model,
        _ATTR_RESPONSE_MODEL: response_model,
        _ATTR_IS_STREAM: is_stream,
        _ATTR_ERROR_TYPE: error_type or "",
        _ATTR_RESPONSE_ERROR_CODE: response_error_code,
    }
    attrs = _merge_extras(attrs, extra_attributes)

    _request_cnt.add(1, attrs)
    _operation_duration.record(duration_s, attrs)
    _time_to_first_token.record(ttft_s, attrs)

    if llm_response is not None and llm_response.usage_metadata is not None:
        usage = llm_response.usage_metadata
        prompt = getattr(usage, "prompt_token_count", None) or 0
        total = getattr(usage, "total_token_count", None) or 0
        if prompt and total:
            _usage_input_tokens.record(prompt, attrs)
            _usage_output_tokens.record(max(total - prompt, 0), attrs)


def report_execute_tool(
    invocation_context: InvocationContext,
    tool: BaseTool,
    *,
    duration_s: float,
    error_type: Optional[str] = None,
    extra_attributes: Optional[Mapping[str, Any]] = None,
) -> None:
    """Record one tool invocation."""
    attrs = {
        _ATTR_OPERATION_NAME: _OP_EXECUTE_TOOL,
        _ATTR_SYSTEM: _infer_system(_agent_model_name(invocation_context.agent)),
        _ATTR_APP_NAME: invocation_context.app_name,
        _ATTR_USER_ID: invocation_context.user_id,
        _ATTR_AGENT_ID: invocation_context.agent_name,
        _ATTR_AGENT_NAME: invocation_context.agent_name,
        _ATTR_TOOL_NAME: tool.name,
        _ATTR_ERROR_TYPE: error_type or "",
    }
    attrs = _merge_extras(attrs, extra_attributes)

    _request_cnt.add(1, attrs)
    _operation_duration.record(duration_s, attrs)


def report_invoke_agent(
    invocation_context: InvocationContext,
    *,
    duration_s: float,
    ttft_s: float,
    input_tokens: int,
    output_tokens: int,
    is_stream: bool,
    error_type: Optional[str] = None,
    extra_attributes: Optional[Mapping[str, Any]] = None,
) -> None:
    """Record one agent run; token counts are aggregated from child LLM calls."""
    attrs = {
        _ATTR_OPERATION_NAME: _OP_INVOKE_AGENT,
        _ATTR_SYSTEM: _infer_system(_agent_model_name(invocation_context.agent)),
        _ATTR_APP_NAME: invocation_context.app_name,
        _ATTR_USER_ID: invocation_context.user_id,
        _ATTR_AGENT_ID: invocation_context.agent_name,
        _ATTR_AGENT_NAME: invocation_context.agent_name,
        _ATTR_IS_STREAM: is_stream,
        _ATTR_ERROR_TYPE: error_type or "",
    }
    attrs = _merge_extras(attrs, extra_attributes)

    _request_cnt.add(1, attrs)
    _operation_duration.record(duration_s, attrs)
    _time_to_first_token.record(ttft_s, attrs)
    if input_tokens:
        _usage_input_tokens.record(input_tokens, attrs)
    if output_tokens:
        _usage_output_tokens.record(output_tokens, attrs)
