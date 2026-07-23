# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from unittest.mock import AsyncMock, Mock, patch

import pytest
from trpc_agent_sdk.models import LlmRequest, OpenAIModel
from trpc_agent_sdk.types import Content, GenerateContentConfig, Part


def _model(**kwargs):
    """Create an OpenAIModel with test defaults."""
    kwargs.setdefault("model_name", "gpt-4")
    kwargs.setdefault("api_key", "test_key")
    return OpenAIModel(**kwargs)


def _request(contents, config=None, streaming_tool_names=None):
    """Create an LlmRequest for Responses API tests."""
    request = LlmRequest(contents=contents, config=config, tools_dict={})
    if streaming_tool_names is not None:
        request.streaming_tool_names = streaming_tool_names
    return request


# ---------------------------------------------------------------------------
# Responses API
# ---------------------------------------------------------------------------


class TestOpenAIResponsesAPI:
    """Tests for the opt-in OpenAI Responses transport."""

    def test_is_disabled_by_default_and_rejects_managed_overrides(self):
        model = _model()
        assert model.use_responses_api is False

        with pytest.raises(ValueError, match="cannot override managed parameters: input"):
            _model(use_responses_api=True, responses_api_params={"input": "override"})

    def test_converts_tool_history_and_function_definitions(self):
        model = _model(use_responses_api=True)
        messages = [
            {
                "role": "assistant",
                "content": "Checking",
                "tool_calls": [
                    {
                        "id": "call_weather",
                        "type": "function",
                        "function": {
                            "name": "weather",
                            "arguments": '{"city":"Shenzhen"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_weather", "content": '{"temperature":30}'},
        ]

        items = model._convert_messages_to_responses_input(messages)
        assert items == [
            {"role": "assistant", "content": "Checking"},
            {
                "type": "function_call",
                "call_id": "call_weather",
                "name": "weather",
                "arguments": '{"city":"Shenzhen"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_weather",
                "output": '{"temperature":30}',
            },
        ]
        tools = model._convert_tools_to_responses_format(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "description": "Get weather",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]
        )
        assert tools == [
            {
                "type": "function",
                "name": "weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {}},
            }
        ]

    def test_converts_multimodal_input(self):
        model = _model(use_responses_api=True)

        items = model._convert_messages_to_responses_input(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Inspect this image",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,AAAA",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ]
        )

        assert items == [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Inspect this image",
                    },
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,AAAA",
                        "detail": "high",
                    },
                ],
            }
        ]

    def test_preserves_empty_reasoning_item_for_tool_continuation(self):
        model = _model(use_responses_api=True)
        raw_reasoning = {
            "id": "rs_empty",
            "type": "reasoning",
            "encrypted_content": "encrypted-reasoning",
            "summary": [],
        }

        response = model._create_responses_response(
            {
                "id": "resp_tool",
                "status": "completed",
                "output": [
                    raw_reasoning,
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "lookup",
                        "arguments": "{}",
                    },
                ],
            }
        )

        thought = response.content.parts[0]
        assert thought.thought is True
        assert thought.text == ""
        replay_request = _request([Content(parts=response.content.parts, role="model")])
        replay_items = model._convert_messages_to_responses_input(model._format_messages(replay_request))
        assert replay_items[0] == raw_reasoning
        assert replay_items[1]["type"] == "function_call"

    def test_converts_structured_output_and_failed_response(self):
        model = _model(use_responses_api=True)
        text = model._convert_response_format_to_responses(
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "answer",
                    "schema": {"type": "object", "properties": {}},
                    "strict": True,
                },
            }
        )
        assert text == {
            "format": {
                "type": "json_schema",
                "name": "answer",
                "schema": {"type": "object", "properties": {}},
                "strict": True,
            },
        }

        response = model._create_responses_response(
            {
                "id": "resp_failed",
                "status": "failed",
                "error": {"code": "server_error", "message": "upstream failed"},
                "output": [],
            }
        )
        assert response.error_code == "server_error"
        assert response.error_message == "upstream failed"

    @pytest.mark.asyncio
    async def test_non_streaming_uses_responses_create_and_maps_output(self):
        model = _model(
            use_responses_api=True,
            responses_api_params={"store": False, "truncation": "auto"},
        )
        request = _request(
            [Content(parts=[Part.from_text(text="Hello")], role="user")],
            GenerateContentConfig(max_output_tokens=128),
        )
        response = Mock()
        response.model_dump.return_value = {
            "id": "resp_123",
            "status": "completed",
            "output": [
                {
                    "id": "rs_123",
                    "type": "reasoning",
                    "encrypted_content": "encrypted-reasoning",
                    "summary": [{"type": "summary_text", "text": "Check context"}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Hello back"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_123",
                    "name": "lookup",
                    "arguments": '{"query":"hello"}',
                },
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 7,
                "total_tokens": 17,
                "input_tokens_details": {"cached_tokens": 4},
                "output_tokens_details": {"reasoning_tokens": 2},
            },
        }
        captured = {}

        async def create(**kwargs):
            captured.update(kwargs)
            return response

        with patch.object(model, "_create_async_client") as client_factory:
            client = AsyncMock()
            client.responses.create = create
            client_factory.return_value = client
            responses = [item async for item in model.generate_async(request, stream=False)]

        assert captured["model"] == "gpt-4"
        assert captured["input"] == [{"role": "user", "content": "Hello"}]
        assert captured["max_output_tokens"] == 128
        assert captured["store"] is False
        assert captured["truncation"] == "auto"
        assert captured["include"] == ["reasoning.encrypted_content"]
        assert "messages" not in captured
        assert "max_completion_tokens" not in captured
        result = responses[0]
        assert result.response_id == "resp_123"
        assert [part.text for part in result.content.parts if part.text] == ["Check context", "Hello back"]
        assert result.content.parts[0].thought is True
        assert result.content.parts[-1].function_call.id == "call_123"
        assert result.usage_metadata.prompt_token_count == 10
        assert result.usage_metadata.candidates_token_count == 7
        assert result.usage_metadata.thoughts_token_count == 2
        assert result.usage_metadata.cache_read_input_tokens == 4

        replay_request = _request([Content(parts=result.content.parts, role="model")])
        replay_items = model._convert_messages_to_responses_input(model._format_messages(replay_request))
        assert replay_items[0] == {
            "id": "rs_123",
            "type": "reasoning",
            "encrypted_content": "encrypted-reasoning",
            "summary": [{"type": "summary_text", "text": "Check context"}],
        }
        assert replay_items[1] == {"role": "assistant", "content": "Hello back"}
        assert replay_items[2]["type"] == "function_call"

    @pytest.mark.asyncio
    async def test_streaming_maps_text_reasoning_tool_calls_and_usage(self):
        model = _model(use_responses_api=True)
        request = _request(
            [Content(parts=[Part.from_text(text="Use a tool")], role="user")],
            streaming_tool_names={"lookup"},
        )
        events = [
            {"type": "response.created", "response": {"id": "resp_stream"}},
            {"type": "response.reasoning_summary_text.delta", "delta": "Thinking"},
            {"type": "response.output_text.delta", "delta": "Working"},
            {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "",
                },
            },
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '{"q":'},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '"x"}'},
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_stream",
                    "status": "completed",
                    "output": [
                        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Thinking"}]},
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "Working"}],
                        },
                        {
                            "type": "function_call",
                            "id": "fc_1",
                            "call_id": "call_1",
                            "name": "lookup",
                            "arguments": '{"q":"x"}',
                        },
                    ],
                    "usage": {"input_tokens": 5, "output_tokens": 4, "total_tokens": 9},
                },
            },
        ]

        async def stream_events():
            for event in events:
                yield event

        with patch.object(model, "_create_async_client") as client_factory:
            client = AsyncMock()
            client.responses.create = AsyncMock(return_value=stream_events())
            client_factory.return_value = client
            responses = [item async for item in model.generate_async(request, stream=True)]

        assert responses[0].content.parts[0].thought is True
        assert responses[0].content.parts[0].text == "Thinking"
        assert responses[1].content.parts[0].text == "Working"
        tool_deltas = [item.content.parts[0].function_call.args["tool_streaming_args"] for item in responses[2:-1]]
        assert tool_deltas == ['{"q":', '"x"}']
        final = responses[-1]
        assert final.partial is False
        assert final.response_id == "resp_stream"
        assert final.content.parts[-1].function_call.args == {"q": "x"}
        assert final.usage_metadata.total_token_count == 9

    @pytest.mark.asyncio
    async def test_streaming_error_event_is_not_reported_as_success(self):
        model = _model(use_responses_api=True)
        request = _request([Content(parts=[Part.from_text(text="Hello")], role="user")])

        async def stream_events():
            yield {
                "type": "error",
                "response_id": "resp_error",
                "code": "server_error",
                "message": "upstream unavailable",
            }

        with patch.object(model, "_create_async_client") as client_factory:
            client = AsyncMock()
            client.responses.create = AsyncMock(return_value=stream_events())
            client_factory.return_value = client
            responses = [item async for item in model.generate_async(request, stream=True)]

        assert responses[-1].response_id == "resp_error"
        assert responses[-1].error_code == "server_error"
        assert responses[-1].error_message == "upstream unavailable"

    @pytest.mark.asyncio
    async def test_streaming_uses_arguments_done_payload_in_fallback(self):
        model = _model(use_responses_api=True)
        request = _request([Content(parts=[Part.from_text(text="Use a tool")], role="user")])

        async def stream_events():
            yield {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "",
                },
            }
            yield {
                "type": "response.function_call_arguments.done",
                "item_id": "fc_1",
                "arguments": '{"query":"done"}',
            }

        with patch.object(model, "_create_async_client") as client_factory:
            client = AsyncMock()
            client.responses.create = AsyncMock(return_value=stream_events())
            client_factory.return_value = client
            responses = [item async for item in model.generate_async(request, stream=True)]

        final = responses[-1]
        assert final.partial is False
        assert final.content.parts[0].function_call.id == "call_1"
        assert final.content.parts[0].function_call.args == {"query": "done"}

    def test_incomplete_response_maps_reason_to_error(self):
        model = _model(use_responses_api=True)

        response = model._create_responses_response(
            {
                "id": "resp_incomplete",
                "status": "incomplete",
                "incomplete_details": {
                    "reason": "max_output_tokens",
                },
                "output": [],
            }
        )

        assert response.error_code == "incomplete"
        assert response.error_message == "max_output_tokens"
