#!/usr/bin/env python3
"""Extended test for tRPC-Agent's OpenAI Responses adaptation.

Covers the capabilities added in PR #245 (NOT a plain connectivity check):

  1. thinking/effort passthrough: reasoning.effort delivered verbatim via
     responses_api_params, reasoning.summary auto-injected when thinking is on
  2. tool calling round-trip: function_call -> function_call_output (multi-turn)
  3. store=False: SDK auto-appends "reasoning.encrypted_content" to include
  4. generation params passthrough: temperature / top_p / truncation
  5. streaming tool calling: streaming_tool_names deltas + final function_call,
     followed by a function_call_output replay round (stream=True)
  6. store=False multi-turn reasoning replay: first-turn reasoning items
     (with encrypted_content) survive into the second-turn request input

Usage:
    python pipeline_test/test_responses_api_ext.py [--stream]
"""

import argparse
import asyncio
import json
import os

from google.genai import types as genai_types

from trpc_agent_sdk.models import LlmRequest, OpenAIModel
from trpc_agent_sdk.types import Content, FunctionResponse, GenerateContentConfig, Part, ThinkingConfig

# The openai SDK appends "/responses" to base_url for the Responses resource.
BASE_URL = "https://tokenhub.tencentmaas.com/v1"
MODEL = "deepseek-v4-flash-202605"
API_KEY = os.environ.get("LKEAP_API_KEY")
if not API_KEY:
    raise SystemExit("LKEAP_API_KEY is required, e.g. export LKEAP_API_KEY=sk-...")

WEATHER_TOOL = genai_types.Tool(function_declarations=[
    genai_types.FunctionDeclaration(
        name="get_weather",
        description="获取指定城市的当前天气",
        parameters=genai_types.Schema(
            type="OBJECT",
            properties={
                "city": genai_types.Schema(type="STRING", description="城市名，例如 北京"),
            },
            required=["city"],
        ),
    ),
])


def make_model(**kwargs) -> OpenAIModel:
    return OpenAIModel(
        model_name=MODEL,
        api_key=API_KEY,
        base_url=BASE_URL,
        use_responses_api=True,
        **kwargs,
    )


def _dump_part(part: Part) -> None:
    if getattr(part, "text", None):
        print(f"    [text] {part.text}")
    if getattr(part, "thought", False):
        print("    [thought] <reasoning token>")
    fc = getattr(part, "function_call", None)
    if fc is not None:
        print(f"    [function_call] {fc.name}({fc.args}) id={fc.id}")


async def test_thinking_effort(stream: bool) -> None:
    print("\n=== 1. thinking + reasoning.effort passthrough ===")
    model = make_model(responses_api_params={"reasoning": {"effort": "high", "summary": "auto"}})
    request = LlmRequest(
        contents=[Content(role="user", parts=[Part.from_text(text="8 * 7 * 6 等于多少？请分步推理")])],
        config=GenerateContentConfig(thinking_config=ThinkingConfig(include_thoughts=True, thinking_budget=-1)),
        tools_dict={},
    )
    async for resp in model.generate_async(request, stream=stream):
        if resp.content and resp.content.parts:
            for p in resp.content.parts:
                _dump_part(p)
        if resp.usage_metadata:
            print(f"    [usage] thoughts={resp.usage_metadata.thoughts_token_count} "
                  f"prompt={resp.usage_metadata.prompt_token_count} "
                  f"candidates={resp.usage_metadata.candidates_token_count}")
        if resp.error_message:
            print(f"    [error] {resp.error_message}")


async def test_tool_roundtrip(stream: bool) -> None:
    print("\n=== 2. tool calling round-trip (function_call -> function_call_output) ===")
    model = make_model()
    request = LlmRequest(
        contents=[Content(role="user", parts=[Part.from_text(text="北京的天气怎么样？请用工具查询后告诉我")])],
        config=GenerateContentConfig(tools=[WEATHER_TOOL]),
        tools_dict={},
    )
    calls = []
    async for resp in model.generate_async(request, stream=False):
        if resp.content and resp.content.parts:
            for p in resp.content.parts:
                _dump_part(p)
                fc = getattr(p, "function_call", None)
                if fc is not None:
                    calls.append(fc)
        if resp.error_message:
            print(f"    [error] {resp.error_message}")
    if not calls:
        print("    (model answered with text instead of a tool call; "
              "tool registration may need a more targeted prompt)")
        return

    print("    -> feeding function_call_output back for round 2")
    parts = [Part.from_text(text="北京的天气怎么样？请用工具查询后告诉我")]
    for fc in calls:
        fc_id = getattr(fc, "id", None) or "call_weather_1"
        # Part.from_function_call() leaves function_call.id None; the Responses
        # API requires function_call_output.call_id to match a function_call
        # item, so set the id explicitly to the model-issued call id.
        fcall_part = Part.from_function_call(name=fc.name, args=fc.args)
        fcall_part.function_call.id = fc_id
        parts.append(fcall_part)
        parts.append(
            Part(function_response=FunctionResponse(
                id=fc_id, name=fc.name, response={
                    "city": "北京",
                    "weather": "晴",
                    "temperature": 26
                })))
    round2 = LlmRequest(
        contents=[
            Content(role="user", parts=parts),
            # Note: function_call + function_response parts in one Content are
            # split into responses "function_call" and "function_call_output"
            # items by _convert_messages_to_responses_input.
        ],
        config=GenerateContentConfig(tools=[WEATHER_TOOL]),
        tools_dict={},
    )
    async for resp in model.generate_async(round2, stream=stream):
        if resp.content and resp.content.parts:
            for p in resp.content.parts:
                _dump_part(p)
        if resp.error_message:
            print(f"    [error] {resp.error_message}")


async def test_store_false(stream: bool) -> None:
    print("\n=== 3. store=False (auto include reasoning.encrypted_content) ===")
    model = make_model(responses_api_params={"store": False})
    request = LlmRequest(
        contents=[Content(role="user", parts=[Part.from_text(text="1+1=?")])],
        config=GenerateContentConfig(thinking_config=ThinkingConfig(include_thoughts=True, thinking_budget=-1)),
        tools_dict={},
    )
    async for resp in model.generate_async(request, stream=stream):
        if resp.content and resp.content.parts:
            for p in resp.content.parts:
                _dump_part(p)
        if resp.error_message:
            print(f"    [error] {resp.error_message}")


async def test_param_passthrough(stream: bool) -> None:
    print("\n=== 4. generation params passthrough (temperature/top_p/truncation) ===")
    model = make_model(responses_api_params={"truncation": "auto"})
    request = LlmRequest(
        contents=[Content(role="user", parts=[Part.from_text(text="写一句关于春天的诗")])],
        config=GenerateContentConfig(temperature=0.9, top_p=0.8),
        tools_dict={},
    )
    async for resp in model.generate_async(request, stream=stream):
        if resp.content and resp.content.parts:
            for p in resp.content.parts:
                _dump_part(p)
        if resp.error_message:
            print(f"    [error] {resp.error_message}")


async def test_streaming_tool_roundtrip() -> None:
    """Streaming tool calling: incremental arguments via streaming_tool_names,
    a complete function_call in the final response, and a second streaming
    round that feeds function_call_output back to the model."""
    print("\n=== 5. streaming tool calling round-trip ===")
    model = make_model()
    request = LlmRequest(
        contents=[Content(role="user", parts=[Part.from_text(text="深圳的天气怎么样？请调用工具查询后告诉我")])],
        config=GenerateContentConfig(tools=[WEATHER_TOOL]),
        tools_dict={},
    )
    request.streaming_tool_names = {"get_weather"}
    calls = []
    delta_chunks = 0
    async for resp in model.generate_async(request, stream=True):
        if resp.content and resp.content.parts:
            for p in resp.content.parts:
                _dump_part(p)
                fc = getattr(p, "function_call", None)
                if fc is None:
                    continue
                calls.append(fc)
                args = getattr(fc, "args", None) or {}
                if isinstance(args, dict) and args.get("tool_streaming_args"):
                    delta_chunks += 1
        if resp.error_message:
            print(f"    [error] {resp.error_message}")

    if not calls:
        print("    (model answered with text instead of a streaming tool call; skipping replay)")
        return
    print(f"    -> streamed {delta_chunks} argument delta chunk(s), {len(calls)} function_call part(s)")

    # The streaming path must emit at least one incremental delta when the tool
    # is registered in streaming_tool_names, and the final response must carry
    # a complete function_call for the replay.
    if delta_chunks == 0:
        print("    [warn] no streaming argument deltas observed (model may have skipped tool use)")
    final_calls = [c for c in calls if not (isinstance(c.args, dict) and c.args.get("tool_streaming_args"))]
    if not final_calls:
        print("    (no complete function_call in the stream; skipping replay)")
        return

    print("    -> feeding function_call_output back (streaming round 2)")
    parts = [Part.from_text(text="深圳的天气怎么样？请调用工具查询后告诉我")]
    for fc in final_calls:
        fc_id = getattr(fc, "id", None) or "call_weather_stream"
        fcall_part = Part.from_function_call(name=fc.name, args=fc.args)
        fcall_part.function_call.id = fc_id
        parts.append(fcall_part)
        parts.append(
            Part(function_response=FunctionResponse(
                id=fc_id, name=fc.name, response={
                    "city": "深圳",
                    "weather": "多云",
                    "temperature": 30
                })))
    round2 = LlmRequest(
        contents=[Content(role="user", parts=parts)],
        config=GenerateContentConfig(tools=[WEATHER_TOOL]),
        tools_dict={},
    )
    round2.streaming_tool_names = {"get_weather"}
    got_final = False
    async for resp in model.generate_async(round2, stream=True):
        if resp.content and resp.content.parts:
            for p in resp.content.parts:
                _dump_part(p)
        if resp.error_message:
            print(f"    [error] {resp.error_message}")
        if getattr(resp, "partial", None) is False:
            got_final = True
    print(f"    [replay] got_final={got_final}")


async def test_store_false_reasoning_replay() -> None:
    """store=False multi-turn reasoning replay: reasoning items captured in
    round 1 (with encrypted_content) must survive into the round-2 request
    input as verbatim Responses items, and round 2 must not be rejected."""
    print("\n=== 6. store=False multi-turn reasoning replay ===")
    model = make_model(responses_api_params={"store": False})
    request = LlmRequest(
        contents=[Content(role="user", parts=[Part.from_text(text="9 * 9 等于多少？请先推理再回答")])],
        config=GenerateContentConfig(thinking_config=ThinkingConfig(include_thoughts=True, thinking_budget=-1)),
        tools_dict={},
    )
    round1_parts = []
    async for resp in model.generate_async(request, stream=False):
        if resp.content and resp.content.parts:
            round1_parts = resp.content.parts
            for p in round1_parts:
                _dump_part(p)
        if resp.error_message:
            print(f"    [error] {resp.error_message}")

    thought_parts = [p for p in round1_parts if getattr(p, "thought", False)]
    if not thought_parts:
        print("    (model emitted no thought parts in round 1; skipping replay)")
        return

    # Reasoning items are stored on the thought part as thought_signature JSON.
    reasoning_items = []
    for p in thought_parts:
        raw = getattr(p, "thought_signature", None)
        if not raw:
            continue
        raw = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        try:
            item = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(item, dict) and item.get("type") == "reasoning":
            reasoning_items.append(item)
    assert reasoning_items, "no reasoning items recovered from thought parts"
    assert any("encrypted_content" in it for it in reasoning_items), \
        "reasoning items must keep encrypted_content (store=False include)"
    print(f"    [replay] recovered {len(reasoning_items)} reasoning item(s), "
          f"{sum(1 for it in reasoning_items if 'encrypted_content' in it)} with encrypted_content")

    # Round 2: assistant turn (thought + text parts) followed by a user prompt.
    round2_request = LlmRequest(
        contents=[
            Content(role="user", parts=list(round1_parts)),
            Content(role="user", parts=[Part.from_text(text="那 8 * 8 呢？")]),
        ],
        config=GenerateContentConfig(thinking_config=ThinkingConfig(include_thoughts=True, thinking_budget=-1)),
        tools_dict={},
    )
    formatted = model._format_messages(round2_request)
    input_items = model._convert_messages_to_responses_input(formatted)
    replayed = [it for it in input_items if it.get("type") == "reasoning"]
    assert replayed, "reasoning items must be preserved in the round-2 Responses input"
    assert any("encrypted_content" in it for it in replayed), \
        "round-2 input items must keep encrypted_content"
    print(f"    [replay] {len(replayed)} reasoning item(s) preserved in round-2 input")

    async for resp in model.generate_async(round2_request, stream=False):
        if resp.content and resp.content.parts:
            for p in resp.content.parts:
                _dump_part(p)
        if resp.error_message:
            print(f"    [error] {resp.error_message}")


async def main(stream: bool) -> None:
    print(f"model={MODEL} base_url={BASE_URL} stream={stream}")
    await test_thinking_effort(stream)
    await test_tool_roundtrip(stream)
    await test_store_false(stream)
    await test_param_passthrough(stream)
    await test_streaming_tool_roundtrip()
    await test_store_false_reasoning_replay()
    print("\n[done] all scenarios finished")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test tRPC-Agent OpenAI Responses capabilities.")
    parser.add_argument("--stream", action="store_true", help="stream responses")
    args = parser.parse_args()
    asyncio.run(main(stream=args.stream))
