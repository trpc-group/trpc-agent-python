#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Black-box replay validation with a real tool-calling agent."""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from tests.sessions.replay_cases import ExpectedOutcome
from tests.sessions.replay_cases import OperationKind
from tests.sessions.replay_cases import REPLAY_CASES
from tests.sessions.replay_cases import ReplayCase
from tests.sessions.replay_cases import ReplayOperation
from tests.sessions.replay_harness import ReplayRunner
from tests.sessions.replay_harness import create_in_memory_backend
from tests.sessions.replay_harness import create_sqlite_backend
from tests.sessions.replay_report import compare_snapshots
from tests.sessions.replay_report import mutate_snapshot
from tests.sessions.replay_report import write_report

API_KEY_ENV = "TRPC_AGENT_API_KEY"
BASE_URL_ENV = "TRPC_AGENT_BASE_URL"
MODEL_NAME_ENV = "TRPC_AGENT_MODEL_NAME"
REPORT_PATH_ENV = "TRPC_REAL_REPLAY_REPORT_PATH"
REAL_AGENT_TIMEOUT_SECONDS = 60
REAL_CASE_ID = "real_agent_tool_memory"
MEMORY_QUERY = "Chinese"
SOURCE_APP_NAME = "real-replay-source"
SOURCE_USER_ID = "real-replay-user"
SOURCE_SESSION_ID = "real-replay-session"


def lookup_user_preference(user_id: str) -> dict[str, str]:
    """Return a deterministic business fact through a real tool call."""
    return {
        "user_id": user_id,
        "preferred_language": "Chinese",
        "notification_channel": "email",
    }


def _model_config() -> tuple[str, str, str]:
    values = tuple(os.getenv(name, "") for name in (API_KEY_ENV, BASE_URL_ENV, MODEL_NAME_ENV))
    if not all(values):
        pytest.skip(f"set {API_KEY_ENV}, {BASE_URL_ENV}, and {MODEL_NAME_ENV} to run the real-model replay")
    return values


async def _capture_real_case() -> ReplayCase:
    api_key, base_url, model_name = _model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)
    agent = LlmAgent(
        name="preference_agent",
        model=model,
        instruction=("Always call lookup_user_preference before answering. "
                     "Report the returned preferred language and notification channel exactly."),
        tools=[FunctionTool(lookup_user_preference)],
    )
    service = InMemorySessionService()
    runner = Runner(app_name=SOURCE_APP_NAME, agent=agent, session_service=service)
    await service.create_session(
        app_name=SOURCE_APP_NAME,
        user_id=SOURCE_USER_ID,
        session_id=SOURCE_SESSION_ID,
    )
    try:
        await asyncio.wait_for(_consume_agent(runner), REAL_AGENT_TIMEOUT_SECONDS)
        session = await service.get_session(
            app_name=SOURCE_APP_NAME,
            user_id=SOURCE_USER_ID,
            session_id=SOURCE_SESSION_ID,
        )
        if session is None:
            raise AssertionError("real agent did not persist its session")
        return _case_from_events(session.events)
    finally:
        await runner.close()


async def _consume_agent(runner: Runner) -> None:
    message = Content(
        parts=[Part.from_text(text="Look up the preferences for user replay-user, then answer with both values.", )])
    async for _ in runner.run_async(
            user_id=SOURCE_USER_ID,
            session_id=SOURCE_SESSION_ID,
            new_message=message,
    ):
        pass


def _case_from_events(events: list[Any]) -> ReplayCase:
    operations = [ReplayOperation(OperationKind.CREATE)]
    event_ids: list[str] = []
    for event in events:
        for part in event.content.parts if event.content else []:
            operation = _part_operation(part, event.author, len(event_ids))
            if operation is not None:
                operations.append(operation)
                event_ids.append(str(operation.payload["event_id"]))
    operations.extend((
        ReplayOperation(OperationKind.STORE_MEMORY),
        ReplayOperation(OperationKind.SEARCH_MEMORY, {"query": MEMORY_QUERY}),
    ))
    part_types = {operation.payload.get("part_type") for operation in operations}
    if not {"function_call", "function_response"}.issubset(part_types):
        raise AssertionError("real model did not complete the required tool round trip")
    return ReplayCase(
        case_id=REAL_CASE_ID,
        operations=tuple(operations),
        expected=ExpectedOutcome(tuple(event_ids)),
    )


def _part_operation(part: Any, author: str, index: int) -> ReplayOperation | None:
    payload: dict[str, Any] = {
        "event_id": f"real-event-{index}",
        "author": author,
    }
    if part.function_call:
        payload.update(part_type="function_call", value=part.function_call.model_dump(exclude_none=True))
    elif part.function_response:
        payload.update(part_type="function_response", value=part.function_response.model_dump(exclude_none=True))
    elif part.text and not part.thought:
        payload["text"] = part.text
    else:
        return None
    return ReplayOperation(OperationKind.APPEND, payload)


async def _replay_pair(replay_case: ReplayCase, db_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    in_memory = await create_in_memory_backend()
    sqlite = await create_sqlite_backend(db_path)
    try:
        left, right = await asyncio.gather(
            ReplayRunner(in_memory).run(replay_case),
            ReplayRunner(sqlite).run(replay_case),
        )
        return left, right
    finally:
        await asyncio.gather(in_memory.close(), sqlite.close())


def _corrupt_first_string(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, str) and child:
                value[key] = f"{child[:-1]}X"
                return True
            if _corrupt_first_string(child):
                return True
    elif isinstance(value, list):
        return any(_corrupt_first_string(child) for child in value)
    return False


def _corrupt_memory(snapshot: dict[str, Any]) -> None:
    if not _corrupt_first_string(snapshot["memory"][MEMORY_QUERY]["memories"]):
        raise AssertionError("no memory text was available to corrupt")


def _corrupt_tool_response(snapshot: dict[str, Any]) -> None:
    for event in snapshot["events"]:
        response = event["content"]["parts"][0].get("function_response")
        if not response:
            continue
        payload = response.get("response", {})
        if isinstance(payload.get("preferred_language"), str):
            payload["preferred_language"] = f"{payload['preferred_language'][:-1]}X"
            return
        if isinstance(payload.get("temperature"), int):
            payload["temperature"] += 1
            return
        if _corrupt_first_string(payload):
            return
    raise AssertionError("no function response was available to corrupt")


def _tool_memory_case() -> ReplayCase:
    source = next(case for case in REPLAY_CASES if case.case_id == "tool_round_trip")
    operations = source.operations + (
        ReplayOperation(
            OperationKind.APPEND,
            {
                "event_id": "tool-memory-fact",
                "author": "agent",
                "text": "The user's preferred language is Chinese.",
            },
        ),
        ReplayOperation(OperationKind.STORE_MEMORY),
        ReplayOperation(OperationKind.SEARCH_MEMORY, {"query": MEMORY_QUERY}),
    )
    expected = ExpectedOutcome(source.expected.event_ids + ("tool-memory-fact", ))
    return ReplayCase("tool_memory_semantic_drift", operations, expected)


def _write_real_report(replay_case: ReplayCase, clean_diffs: list[Any], injected: dict[str, list[Any]]) -> None:
    report_path = os.getenv(REPORT_PATH_ENV)
    if not report_path:
        return
    tool_operations = [
        operation.payload["part_type"] for operation in replay_case.operations
        if operation.payload.get("part_type") in {"function_call", "function_response"}
    ]
    report = {
        "schema_version": 1,
        "case_id": replay_case.case_id,
        "model": os.environ[MODEL_NAME_ENV],
        "tool_round_trip": tool_operations == ["function_call", "function_response"],
        "clean_comparison": {
            "unexpected_diff_count": len(clean_diffs),
            "diffs": [asdict(diff) for diff in clean_diffs],
        },
        "injected_anomalies": {
            name: {
                "detected": bool(diffs),
                "unexpected_diff_count": len(diffs),
                "diffs": [asdict(diff) for diff in diffs],
            }
            for name, diffs in injected.items()
        },
    }
    write_report(report, Path(report_path))


async def test_subtle_realistic_drift_is_detected(tmp_path: Path) -> None:
    left, right = await _replay_pair(_tool_memory_case(), tmp_path / "mock-drift.db")
    assert compare_snapshots(left, right) == []

    tool_diffs = compare_snapshots(left, mutate_snapshot(right, _corrupt_tool_response))
    assert any(diff.category == "events" and "function_response" in diff.field_path for diff in tool_diffs)

    memory_diffs = compare_snapshots(left, mutate_snapshot(right, _corrupt_memory))
    assert any(diff.category == "memory" and diff.left != diff.right for diff in memory_diffs)
    assert all(not diff.allowed for diff in tool_diffs + memory_diffs)


async def test_real_agent_tool_trace_replays_consistently(tmp_path: Path) -> None:
    replay_case = await _capture_real_case()
    left, right = await _replay_pair(replay_case, tmp_path / "real-agent.db")
    clean_diffs = compare_snapshots(left, right)
    assert clean_diffs == []

    tool_diffs = compare_snapshots(left, mutate_snapshot(right, _corrupt_tool_response))
    memory_diffs = compare_snapshots(left, mutate_snapshot(right, _corrupt_memory))
    assert any(diff.category == "events" and not diff.allowed for diff in tool_diffs)
    assert any(diff.category == "memory" and not diff.allowed for diff in memory_diffs)
    _write_real_report(
        replay_case,
        clean_diffs,
        {
            "tool_response_single_character_drift": tool_diffs,
            "memory_single_character_drift": memory_diffs,
        },
    )
