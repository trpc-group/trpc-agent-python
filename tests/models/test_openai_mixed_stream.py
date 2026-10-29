# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from trpc_agent_sdk.models import LlmRequest, OpenAIModel
from trpc_agent_sdk.models._openai_model import const


async def collect(deltas, streaming_names=None, usage_only=True):
    chunks = [{"id": "synthetic-response", "choices": [{"delta": delta, "finish_reason": None}]} for delta in deltas]
    usage = {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}
    chunks.append({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
    if usage_only:
        chunks.append({"choices": [], "usage": usage})
    else:
        chunks[0]["usage"] = usage

    async def stream():
        for chunk in chunks:
            yield Mock(model_dump=lambda value=chunk: value)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=stream()))),
                             close=AsyncMock())
    model = OpenAIModel(model_name="synthetic", api_key="synthetic")
    request = LlmRequest()
    request.streaming_tool_names = streaming_names
    with patch.object(OpenAIModel, "_create_async_client", return_value=client):
        return [response async for response in model._generate_stream({}, request)]


def tool_delta(index, arguments, first=True):
    result = {"index": index, "function": {"arguments": arguments}}
    if first:
        result.update(id=f"call_{index}", type="function")
        result["function"]["name"] = f"tool_{index}"
    return result


@pytest.mark.parametrize("text", [{}, {
    "content": "text"
}, {
    "reasoning_content": "synthetic"
}, {
    "content": "text",
    "reasoning_content": "synthetic"
}, {
    "content": "",
    "reasoning_content": None
}])
@pytest.mark.parametrize("fragmented", [False, True])
@pytest.mark.parametrize("tool_count", [1, 2])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_mixed_stream_preserves_calls_and_text(text, fragmented, tool_count, streaming):
    arguments = json.dumps({"value": "中文", "count": 12}, ensure_ascii=False)
    fragments = [arguments[:12], arguments[12:]] if fragmented else [arguments]
    deltas = [
        dict(text, tool_calls=[tool_delta(index, fragment, first=part == 0) for index in range(tool_count)])
        for part, fragment in enumerate(fragments)
    ]
    responses = await collect(deltas, {"tool_0"} if streaming else set(), usage_only=fragmented)
    final = responses[-1]
    assert not final.partial
    assert final.error_code is None
    assert final.response_id == "synthetic-response"
    assert final.usage_metadata.total_token_count == 10
    calls = [part.function_call for part in final.content.parts if part.function_call]
    assert [(call.id, call.name, call.args) for call in calls] == [(f"call_{index}", f"tool_{index}", {
        "value": "中文",
        "count": 12
    }) for index in range(tool_count)]
    for thought, field in [(False, "content"), (True, "reasoning_content")]:
        expected = (text.get(field) or "") * len(fragments)
        assert "".join(part.text for part in final.content.parts
                       if part.text and bool(part.thought) == thought) == expected
        assert "".join(part.text for response in responses[:-1] if response.content for part in response.content.parts
                       if part.text and bool(part.thought) == thought) == expected
    partial_calls = [
        part.function_call for response in responses[:-1] if response.content for part in response.content.parts
        if part.function_call
    ]
    assert [(call.id, call.name, call.args) for call in partial_calls] == ([("call_0", "tool_0", {
        const.TOOL_STREAMING_ARGS: fragment
    }) for fragment in fragments] if streaming else [])


@pytest.mark.asyncio
async def test_separate_text_and_tool_chunks():
    responses = await collect([{"content": "text"}, {"tool_calls": [tool_delta(0, '{"ok": true}')]}])
    parts = responses[-1].content.parts
    assert parts[0].text == "text"
    assert parts[1].function_call.args == {"ok": True}


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.asyncio
async def test_invalid_json_preserves_error_envelope(mixed):
    delta = {"tool_calls": [tool_delta(0, 'not-json'), tool_delta(1, '{"ok": true}')]}
    if mixed:
        delta["content"] = "text"
    responses = await collect([delta])
    final = responses[-1]
    calls = [part.function_call for part in final.content.parts if part.function_call]
    assert [call.name for call in calls] == ["tool_0", "tool_1"]
    assert const.TOOL_CALL_ARGUMENT_ERRORS in calls[0].args
    assert calls[1].args == {"ok": True}
    assert final.error_code is None


@pytest.mark.parametrize("mixed_index", [0, 1, 2])
@pytest.mark.asyncio
async def test_one_mixed_fragment_cannot_silently_change_arguments(mixed_index):
    fragments = ['{"count":1', '2', '}']
    deltas = [{"tool_calls": [tool_delta(0, fragment, first=index == 0)]} for index, fragment in enumerate(fragments)]
    deltas[mixed_index]["content"] = "text"
    responses = await collect(deltas, {"tool_0"})
    calls = [part.function_call for part in responses[-1].content.parts if part.function_call]
    assert len(calls) == 1
    assert calls[0].id == "call_0"
    assert calls[0].args == {"count": 12}
    streamed = [
        part.function_call.args[const.TOOL_STREAMING_ARGS] for response in responses[:-1] if response.content
        for part in response.content.parts if part.function_call
    ]
    assert streamed == fragments


@pytest.mark.asyncio
async def test_mixed_chunk_text_precedes_streaming_tool_event():
    responses = await collect([{
        "content": "Recording now.",
        "tool_calls": [tool_delta(0, '{"count":12}')]
    }], {"tool_0"})
    assert responses[0].partial
    assert responses[0].content.parts[0].text == "Recording now."
    assert responses[1].partial
    assert responses[1].content.parts[0].function_call.name == "tool_0"


@pytest.mark.asyncio
async def test_mixed_chunks_keep_text_through_anthropic_proxy():
    pytest.importorskip("claude_agent_sdk")
    from trpc_agent_sdk.server.agents.claude._proxy import AnthropicMessagesRequest, AnthropicProxyApp

    responses = await collect([
        {
            "content": "Recording ",
            "tool_calls": [tool_delta(0, '{"count":')]
        },
        {
            "content": "now.",
            "tool_calls": [tool_delta(0, '12}', first=False)]
        },
    ], {"tool_0"})

    async def generate(request, stream=True):
        for response in responses:
            yield response

    model = SimpleNamespace(generate_async=generate)
    request = AnthropicMessagesRequest(model="synthetic", messages=[{"role": "user", "content": "record"}], stream=True)
    events = [event async for event in AnthropicProxyApp()._handle_streaming(LlmRequest(), request, model)]
    payloads = [
        json.loads(line[6:]) for event in events for line in event.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    deltas = [payload["delta"] for payload in payloads if payload["type"] == "content_block_delta"]
    assert "".join(delta["text"] for delta in deltas if delta["type"] == "text_delta") == "Recording now."
    assert "".join(delta["partial_json"] for delta in deltas if delta["type"] == "input_json_delta") == '{"count":12}'

    starts = [payload["index"] for payload in payloads if payload["type"] == "content_block_start"]
    stops = [payload["index"] for payload in payloads if payload["type"] == "content_block_stop"]
    assert len(starts) == len(set(starts))
    assert sorted(starts) == sorted(stops)


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.asyncio
async def test_streaming_tool_events_keep_response_identity_and_chunk_metadata(mixed):
    deltas = [
        {
            "tool_calls": [tool_delta(0, '{"count":')]
        },
        {
            "tool_calls": [tool_delta(0, '12}', first=False)]
        },
    ]
    if mixed:
        for delta in deltas:
            delta["content"] = "text"
    responses = await collect(deltas, {"tool_0"})
    tool_events = [
        response for response in responses[:-1] if any(part.function_call for part in response.content.parts)
    ]
    assert len(tool_events) == 2
    for event, delta in zip(tool_events, deltas):
        assert event.response_id == responses[-1].response_id == "synthetic-response"
        assert event.custom_metadata[const.TOOL_STREAMING] is True
        assert event.custom_metadata[const.CHUNK]["choices"][0]["delta"] == delta


@pytest.mark.asyncio
async def test_fragmented_invalid_json_in_mixed_chunks_keeps_error_envelope():
    responses = await collect([
        {
            "content": "Recording ",
            "tool_calls": [tool_delta(0, 'not-')]
        },
        {
            "content": "now.",
            "tool_calls": [tool_delta(0, 'json', first=False)]
        },
    ], {"tool_0"})
    assert [bool(response.content.parts[0].text) for response in responses[:-1]] == [True, False, True, False]
    call = next(part.function_call for part in responses[-1].content.parts if part.function_call)
    assert call.name == "tool_0"
    assert const.TOOL_CALL_ARGUMENT_ERRORS in call.args
    assert responses[-1].error_code is None


@pytest.mark.asyncio
async def test_proxy_closes_open_blocks_when_interleaved_stream_fails():
    pytest.importorskip("claude_agent_sdk")
    from trpc_agent_sdk.server.agents.claude._proxy import AnthropicMessagesRequest, AnthropicProxyApp

    responses = await collect([
        {
            "content": "Recording ",
            "tool_calls": [tool_delta(0, '{"count":')]
        },
        {
            "content": "now."
        },
    ], {"tool_0"})

    async def generate(request, stream=True):
        for response in responses[:-1]:
            yield response
        raise RuntimeError("test stream interrupted")

    request = AnthropicMessagesRequest(model="synthetic", messages=[{"role": "user", "content": "record"}], stream=True)
    model = SimpleNamespace(generate_async=generate)
    events = [event async for event in AnthropicProxyApp()._handle_streaming(LlmRequest(), request, model)]
    payloads = [
        json.loads(line[6:]) for event in events for line in event.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    open_blocks = set()
    for payload in payloads:
        if payload["type"] == "content_block_start":
            assert payload["index"] not in open_blocks
            open_blocks.add(payload["index"])
        elif payload["type"] == "content_block_stop":
            assert payload["index"] in open_blocks
            open_blocks.remove(payload["index"])
        elif payload["type"] in {"message_delta", "message_stop"}:
            assert not open_blocks
    assert not open_blocks
    assert next(p for p in payloads if p["type"] == "message_delta")["delta"]["stop_reason"] == "error"
