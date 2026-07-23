# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from unittest.mock import AsyncMock, Mock, patch

import pytest
from trpc_agent_sdk.models import LlmRequest, OpenAIModel
from trpc_agent_sdk.types import Content, GenerateContentConfig, HttpOptions, Part


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

    def test_logprobs_uses_the_installed_responses_client_shape(self):
        model = _model(use_responses_api=True)
        params = model._convert_api_params_to_responses(
            {
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
                "logprobs": True,
                "top_logprobs": 3,
            }
        )

        class TopLogprobsResponses:
            async def create(self, *, top_logprobs, **kwargs):
                del top_logprobs, kwargs

        class TopLogprobsClient:
            responses = TopLogprobsResponses()

        prepared = model._prepare_responses_api_params(TopLogprobsClient(), params)
        assert prepared["top_logprobs"] == 3
        assert "_trpc_responses_logprobs_request" not in prepared

    def test_logprobs_fails_clearly_when_the_installed_client_lacks_support(self):
        model = _model(use_responses_api=True)
        params = model._convert_api_params_to_responses(
            {
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
                "logprobs": True,
                "top_logprobs": 3,
            }
        )

        class LegacyResponses:
            async def create(self, *, model, input, stream):
                del model, input, stream

        class LegacyClient:
            responses = LegacyResponses()

        with pytest.raises(ValueError, match="upgrade openai or disable logprobs"):
            model._prepare_responses_api_params(LegacyClient(), params)

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
    async def test_non_streaming_passes_http_options_separately(self):
        """Non-streaming Responses path must pass http_options (extra_body,
        extra_headers, timeout) as separate kwargs to responses.create,
        not merged into api_params (which would cause extra_body to be
        treated as an unknown top-level parameter).
        """
        model = _model(use_responses_api=True)
        request = _request(
            [Content(parts=[Part.from_text(text="Hello")], role="user")],
            GenerateContentConfig(
                max_output_tokens=128,
                http_options=HttpOptions(
                    headers={"X-Custom-Header": "test-value"},
                    timeout=5000,
                    extra_body={"custom_param": "custom_value"},
                ),
            ),
        )
        response = Mock()
        response.model_dump.return_value = {
            "id": "resp_456",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Hi there"}],
                },
            ],
            "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
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

        # Core API params must be present.
        assert captured["model"] == "gpt-4"
        assert captured["input"] == [{"role": "user", "content": "Hello"}]
        assert captured["max_output_tokens"] == 128

        # http_options must be passed as separate kwargs, not merged into
        # the api_params dict that goes through _prepare_responses_api_params.
        assert captured["extra_headers"] == {"X-Custom-Header": "test-value"}
        assert captured["extra_body"] == {"custom_param": "custom_value"}
        assert captured["timeout"] == 5.0  # 5000ms / 1000

        # The response should be properly mapped.
        result = responses[0]
        assert result.response_id == "resp_456"
        assert [part.text for part in result.content.parts if part.text] == ["Hi there"]

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

    # ------------------------------------------------------------------
    # _responses_error — cancelled / incomplete_details fallback
    # ------------------------------------------------------------------

    def test_cancelled_response_maps_status_to_error(self):
        model = _model(use_responses_api=True)
        response = model._create_responses_response(
            {
                "id": "resp_cancelled",
                "status": "cancelled",
                "output": [],
            }
        )
        assert response.error_code == "cancelled"
        assert response.error_message == "cancelled"

    def test_incomplete_details_non_dict_fallback(self):
        model = _model(use_responses_api=True)
        response = model._create_responses_response(
            {
                "id": "resp_incomplete",
                "status": "incomplete",
                "incomplete_details": "timeout",
                "output": [],
            }
        )
        assert response.error_code == "incomplete"
        assert response.error_message == "timeout"

    # ------------------------------------------------------------------
    # _create_responses_response — refusal text / non-dict arguments
    # ------------------------------------------------------------------

    def test_message_with_refusal_text(self):
        model = _model(use_responses_api=True)
        response = model._create_responses_response(
            {
                "id": "resp_refusal",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "refusal", "refusal": "I cannot answer that."}],
                    }
                ],
            }
        )
        assert response.content.parts[0].text == "I cannot answer that."

    def test_function_call_non_dict_arguments_skipped(self):
        model = _model(use_responses_api=True)
        response = model._create_responses_response(
            {
                "id": "resp_bad_args",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_bad",
                        "name": "broken_tool",
                        "arguments": "[not a dict]",
                    }
                ],
            }
        )
        # Non-dict arguments are skipped, so no parts from function_call
        assert response.content is None

    # ------------------------------------------------------------------
    # _convert_messages_to_responses_input — assistant / unknown type
    # ------------------------------------------------------------------

    def test_converts_assistant_message_to_output_text(self):
        model = _model(use_responses_api=True)
        items = model._convert_messages_to_responses_input(
            [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello from assistant"}],
                }
            ]
        )
        assert items == [
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hello from assistant"}],
            }
        ]

    def test_unknown_content_type_is_passthrough(self):
        model = _model(use_responses_api=True)
        items = model._convert_messages_to_responses_input(
            [
                {
                    "role": "user",
                    "content": [{"type": "custom_block", "data": "raw"}],
                }
            ]
        )
        assert items == [
            {
                "role": "user",
                "content": [{"type": "custom_block", "data": "raw"}],
            }
        ]

    def test_converts_non_function_tool_to_responses_format(self):
        model = _model(use_responses_api=True)
        tools = model._convert_tools_to_responses_format(
            [
                {"type": "web_search", "name": "search"},
                {
                    "type": "function",
                    "function": {
                        "name": "calc",
                        "description": "Calculate",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ]
        )
        assert tools == [
            {"type": "web_search", "name": "search"},
            {
                "type": "function",
                "name": "calc",
                "description": "Calculate",
                "parameters": {"type": "object", "properties": {}},
            },
        ]

    # ------------------------------------------------------------------
    # _prepare_responses_api_params — logprobs structured support
    # ------------------------------------------------------------------

    def test_logprobs_uses_structured_logprobs_when_supported(self):
        model = _model(use_responses_api=True)
        params = model._convert_api_params_to_responses(
            {
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
                "logprobs": True,
                "top_logprobs": 3,
            }
        )

        class LogprobsResponses:
            async def create(self, *, logprobs, **kwargs):
                del logprobs, kwargs

        class LogprobsClient:
            responses = LogprobsResponses()

        prepared = model._prepare_responses_api_params(LogprobsClient(), params)
        assert prepared["logprobs"] == {"enabled": True, "top_logprobs": 3}

    def test_logprobs_raises_on_inspect_failure(self):
        model = _model(use_responses_api=True)
        params = model._convert_api_params_to_responses(
            {
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
                "logprobs": True,
                "top_logprobs": 3,
            }
        )

        class BrokenResponses:
            create = "not_callable"

        class BrokenClient:
            responses = BrokenResponses()

        with pytest.raises(ValueError, match="Unable to determine Responses logprobs support"):
            model._prepare_responses_api_params(BrokenClient(), params)

    # ------------------------------------------------------------------
    # _generate_responses_stream — fallback when no completed event
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_streaming_fallback_when_no_completed_event(self):
        """Streaming without response.completed builds final from accumulated text."""
        model = _model(use_responses_api=True)
        request = _request(
            [Content(parts=[Part.from_text(text="Hello")], role="user")],
            streaming_tool_names={"search"},
        )

        async def stream_events():
            yield {"type": "response.created", "response": {"id": "resp_fallback"}}
            yield {"type": "response.reasoning_summary_text.delta", "delta": "Hmm"}
            yield {"type": "response.output_text.delta", "delta": "Answer"}
            yield {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "id": "fc_fb",
                    "call_id": "call_fb",
                    "name": "search",
                    "arguments": "",
                },
            }
            yield {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_fb",
                "delta": '{"q":"x"}',
            }
            # No response.completed — triggers fallback

        with patch.object(model, "_create_async_client") as client_factory:
            client = AsyncMock()
            client.responses.create = AsyncMock(return_value=stream_events())
            client_factory.return_value = client
            responses = [item async for item in model.generate_async(request, stream=True)]

        final = responses[-1]
        assert final.partial is False
        assert final.response_id == "resp_fallback"
        assert final.content is not None
        texts = [p.text for p in final.content.parts if p.text]
        assert "Hmm" in texts or "Answer" in texts

    @pytest.mark.asyncio
    async def test_streaming_arguments_done_with_function_call_item(self):
        """response.function_call_arguments.done with item.type=function_call uses the item."""
        model = _model(use_responses_api=True)
        request = _request([Content(parts=[Part.from_text(text="Use a tool")], role="user")])

        async def stream_events():
            yield {"type": "response.created", "response": {"id": "resp_done_item"}}
            yield {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "id": "fc_item",
                    "call_id": "call_item",
                    "name": "lookup",
                    "arguments": "",
                },
            }
            yield {
                "type": "response.function_call_arguments.done",
                "item_id": "fc_item",
                "item": {
                    "type": "function_call",
                    "id": "fc_item",
                    "call_id": "call_item",
                    "name": "lookup",
                    "arguments": '{"result":"from_item"}',
                },
            }
            yield {
                "type": "response.completed",
                "response": {
                    "id": "resp_done_item",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "id": "fc_item",
                            "call_id": "call_item",
                            "name": "lookup",
                            "arguments": '{"result":"from_item"}',
                        }
                    ],
                },
            }

        with patch.object(model, "_create_async_client") as client_factory:
            client = AsyncMock()
            client.responses.create = AsyncMock(return_value=stream_events())
            client_factory.return_value = client
            responses = [item async for item in model.generate_async(request, stream=True)]

        final = responses[-1]
        assert final.partial is False
        # Function call from item should be in final output
        assert final.content is not None

    @pytest.mark.asyncio
    async def test_streaming_response_incomplete_event(self):
        """response.incomplete event sets completed_response."""
        model = _model(use_responses_api=True)
        request = _request([Content(parts=[Part.from_text(text="Hello")], role="user")])

        async def stream_events():
            yield {"type": "response.created", "response": {"id": "resp_inc"}}
            yield {
                "type": "response.incomplete",
                "response": {
                    "id": "resp_inc",
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "output": [],
                },
            }

        with patch.object(model, "_create_async_client") as client_factory:
            client = AsyncMock()
            client.responses.create = AsyncMock(return_value=stream_events())
            client_factory.return_value = client
            responses = [item async for item in model.generate_async(request, stream=True)]

        final = responses[-1]
        assert final.error_code == "incomplete"
        assert final.error_message == "max_output_tokens"

    # ------------------------------------------------------------------
    # _convert_api_params_to_responses — max_tokens fallback
    # ------------------------------------------------------------------

    def test_max_tokens_falls_back_to_max_output_tokens(self):
        model = _model(use_responses_api=True)
        params = model._convert_api_params_to_responses(
            {
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
                "max_tokens": 256,
            }
        )
        assert params["max_output_tokens"] == 256
        assert "max_tokens" not in params
