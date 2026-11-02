"""The HTTP mock must speak enough OpenAI wire format for the SDK clients."""

from __future__ import annotations

import time

import pytest

from openai import OpenAI

from trpc_agent_bench.core import FunctionBlock
from trpc_agent_bench.core import ModelConf
from trpc_agent_bench.core import TextBlock
from trpc_agent_bench.core._model import MockOpenAIServer


@pytest.fixture()
def text_server():
    server = MockOpenAIServer([[TextBlock("ok", chunks=2)]])
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture()
def tool_server():
    server = MockOpenAIServer([
        [
            [FunctionBlock("add_numbers", {
                "a": 1,
                "b": 2
            })],
            TextBlock("ok"),
        ],
    ])
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_non_stream_text(text_server: MockOpenAIServer) -> None:
    client = OpenAI(api_key="bench", base_url=text_server.base_url)
    response = client.chat.completions.create(
        model="bench-model",
        messages=[{
            "role": "user",
            "content": "hi"
        }],
    )
    assert response.choices[0].message.content == "ok"
    assert response.choices[0].finish_reason == "stop"


def test_stream_text(text_server: MockOpenAIServer) -> None:
    client = OpenAI(api_key="bench", base_url=text_server.base_url)
    stream = client.chat.completions.create(
        model="bench-model",
        messages=[{
            "role": "user",
            "content": "hi"
        }],
        stream=True,
        stream_options={"include_usage": True},
    )
    chunks = []
    usage = None
    for chunk in stream:
        if chunk.choices:
            delta = chunk.choices[0].delta
            if delta.content:
                chunks.append(delta.content)
        elif chunk.usage:
            usage = chunk.usage
    assert "".join(chunks) == "ok"
    assert usage is not None and usage.total_tokens == 17


def test_stream_applies_request_and_chunk_latency() -> None:
    server = MockOpenAIServer(
        [[TextBlock("abcdef", chunks=3)]],
        ModelConf(request_delay_ms=100, chunk_delay_ms=20),
    )
    server.start()
    try:
        client = OpenAI(api_key="bench", base_url=server.base_url)
        started = time.perf_counter()
        stream = client.chat.completions.create(
            model="bench-model",
            messages=[{
                "role": "user",
                "content": "hi"
            }],
            stream=True,
        )
        first_chunk_at = None
        chunks = []
        for chunk in stream:
            if first_chunk_at is None:
                first_chunk_at = time.perf_counter() - started
            if chunk.choices:
                content = chunk.choices[0].delta.content
                if content:
                    chunks.append(content)
        elapsed = time.perf_counter() - started
    finally:
        server.stop()

    assert "".join(chunks) == "abcdef"
    assert first_chunk_at is not None
    assert first_chunk_at >= 0.08
    assert elapsed >= 0.11


def test_stream_tool_call(tool_server: MockOpenAIServer) -> None:
    client = OpenAI(api_key="bench", base_url=tool_server.base_url)
    stream = client.chat.completions.create(
        model="bench-model",
        messages=[{
            "role": "user",
            "content": "add"
        }],
        stream=True,
    )
    calls = []
    finish_reason = None
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.tool_calls:
            calls.extend(delta.tool_calls)
        if chunk.choices[0].finish_reason:
            finish_reason = chunk.choices[0].finish_reason
    assert finish_reason == "tool_calls"
    assert len(calls) == 1
    assert calls[0].function.name == "add_numbers"
    assert calls[0].function.arguments == '{"a": 1, "b": 2}'


def test_non_stream_after_tool_result(tool_server: MockOpenAIServer) -> None:
    client = OpenAI(api_key="bench", base_url=tool_server.base_url)
    response = client.chat.completions.create(
        model="bench-model",
        messages=[
            {
                "role": "user",
                "content": "add"
            },
            {
                "role":
                "assistant",
                "content":
                None,
                "tool_calls": [
                    {
                        "id": "call_1_0_0",
                        "type": "function",
                        "function": {
                            "name": "add_numbers",
                            "arguments": '{"a": 1, "b": 2}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "content": "3"
            },
        ],
    )
    assert response.choices[0].message.content == "ok"
