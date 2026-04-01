# -*- coding: utf-8 -*-
"""Interactive AG-UI Python SDK client for generated service."""

import asyncio
import json
import uuid
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import AsyncGenerator

import aiohttp
from ag_ui.core import Event
from ag_ui.core import RunAgentInput
from ag_ui.core import RunErrorEvent
from ag_ui.core import RunFinishedEvent
from ag_ui.core import RunStartedEvent
from ag_ui.core import StateDeltaEvent
from ag_ui.core import TextMessageContentEvent
from ag_ui.core import TextMessageEndEvent
from ag_ui.core import ToolCallArgsEvent
from ag_ui.core import ToolCallEndEvent
from ag_ui.core import ToolCallResultEvent
from ag_ui.core import ToolCallStartEvent
from ag_ui.core import UserMessage
from pydantic import TypeAdapter

EVENT_ADAPTER = TypeAdapter(Event)
NODE_METADATA_PATH = "/_node_metadata"


@dataclass
class StreamPrintState:
    """Track stream formatting state for pretty terminal output."""

    text_streaming: bool = False
    tool_names: dict[str, str] = field(default_factory=dict)
    tool_args: dict[str, str] = field(default_factory=dict)


def _end_text_stream(state: StreamPrintState) -> None:
    if state.text_streaming:
        print()
        state.text_streaming = False


def _new_run_input(thread_id: str, user_text: str) -> RunAgentInput:
    user_message = UserMessage(id=str(uuid.uuid4()), role="user", content=user_text)
    return RunAgentInput(
        thread_id=thread_id,
        run_id=str(uuid.uuid4()),
        state={},
        messages=[user_message],
        tools=[],
        context=[],
        forwarded_props={},
    )


async def _iter_sse_payloads(response: aiohttp.ClientResponse) -> AsyncGenerator[str, None]:
    data_lines: list[str] = []
    async for raw_line in response.content:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "":
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())

    if data_lines:
        yield "\n".join(data_lines)


def _print_node_metadata(metadata: dict[str, Any]) -> None:
    phase = str(metadata.get("phase", ""))
    node_type = str(metadata.get("node_type", "unknown"))
    node_name = str(metadata.get("node_id", "unknown"))
    if phase == "start":
        print(f"  [Node start] node_type={node_type}, node_name={node_name}")
    elif phase == "complete":
        print(f"  [Node done ] node_type={node_type}, node_name={node_name}")
    elif phase == "error":
        print(f"  [Node error] node_type={node_type}, node_name={node_name}")
        error = metadata.get("error")
        if error:
            print(f"  Error: {error}")


def _print_state_delta(event: StateDeltaEvent, state: StreamPrintState) -> None:
    for patch in event.delta:
        if not isinstance(patch, dict):
            continue
        path = patch.get("path")
        value = patch.get("value")
        if path == NODE_METADATA_PATH and isinstance(value, dict):
            _end_text_stream(state)
            _print_node_metadata(value)


def _compact_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _print_event(event: Event, state: StreamPrintState) -> None:
    if isinstance(event, StateDeltaEvent):
        _print_state_delta(event, state)
        return

    if isinstance(event, RunStartedEvent):
        _end_text_stream(state)
        print(f"  [Run start] run_id={event.run_id}")
        return

    if isinstance(event, RunFinishedEvent):
        _end_text_stream(state)
        print(f"  [Run done ] run_id={event.run_id}")
        return

    if isinstance(event, RunErrorEvent):
        _end_text_stream(state)
        print(f"  [Run error] code={event.code}, message={event.message}")
        return

    if isinstance(event, ToolCallStartEvent):
        state.tool_names[event.tool_call_id] = event.tool_call_name
        state.tool_args[event.tool_call_id] = ""
        return

    if isinstance(event, ToolCallArgsEvent):
        previous = state.tool_args.get(event.tool_call_id, "")
        state.tool_args[event.tool_call_id] = previous + event.delta
        return

    if isinstance(event, ToolCallEndEvent):
        _end_text_stream(state)
        tool_name = state.tool_names.pop(event.tool_call_id, "unknown_tool")
        args_text = state.tool_args.pop(event.tool_call_id, "")
        print(f"  [Function call] {tool_name}({args_text})")
        return

    if isinstance(event, ToolCallResultEvent):
        _end_text_stream(state)
        print(f"  [Function result] {_compact_text(event.content)}")
        return

    if isinstance(event, TextMessageContentEvent):
        if not state.text_streaming:
            print("  ", end="", flush=True)
            state.text_streaming = True
        print(event.delta, end="", flush=True)
        return

    if isinstance(event, TextMessageEndEvent):
        _end_text_stream(state)


async def _run_query(session: aiohttp.ClientSession, url: str, thread_id: str, user_text: str) -> None:
    state = StreamPrintState()
    payload = _new_run_input(thread_id=thread_id, user_text=user_text).model_dump(by_alias=True, exclude_none=True)
    print("Assistant:")
    async with session.post(url, json=payload, headers={"accept": "text/event-stream"}) as response:
        response.raise_for_status()
        async for payload_text in _iter_sse_payloads(response):
            if payload_text == "":
                continue
            try:
                event = EVENT_ADAPTER.validate_json(payload_text)
            except Exception:
                _end_text_stream(state)
                print(f"  [Raw event] {payload_text}")
                continue
            _print_event(event, state)
    _end_text_stream(state)


async def run_agui_client() -> None:
    """Interactive generated AG-UI client."""
    url = "http://{{ service_host }}:{{ service_port }}{{ agui_uri }}"
    thread_id = str(uuid.uuid4())

    print("Interactive mode. Type 'exit' to quit, 'new' for new session.")
    timeout = aiohttp.ClientTimeout(total=None)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                user_text = input("You: ").strip()
            except EOFError:
                print("\nGoodbye!")
                break
            except KeyboardInterrupt:
                print("\nGoodbye!")
                break

            if not user_text:
                continue

            lowered = user_text.lower()
            if lowered in {"exit", "quit"}:
                print("Goodbye!")
                break

            if lowered == "new":
                thread_id = str(uuid.uuid4())
                print(f"New session: {thread_id}")
                continue

            try:
                await _run_query(session=session, url=url, thread_id=thread_id, user_text=user_text)
            except aiohttp.ClientResponseError as error:
                print(f"Error: HTTP {error.status} {error.message}")
            except Exception as error:
                print(f"Error: {error}")


if __name__ == "__main__":
    asyncio.run(run_agui_client())
