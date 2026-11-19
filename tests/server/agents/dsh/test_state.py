# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import json

import pytest

from trpc_agent_sdk.events import EventActions
from trpc_agent_sdk.server.agents.dsh._protocol import DSH_SESSION_STATE_KEY
from trpc_agent_sdk.server.agents.dsh._protocol import DSHRunInput
from trpc_agent_sdk.server.agents.dsh._state import DSHState

_HEADER = {"type": "session", "version": 4, "id": "s1", "createdAt": 1,
           "isSeeded": False, "delegationDepth": 0}


def _jsonl(*events):
    lines = [json.dumps(_HEADER)]
    lines.extend(json.dumps(event) for event in events)
    return "\n".join(lines) + "\n"


class _Session:

    def __init__(self, state=None):
        self.state = state or {}


class _Context:

    def __init__(self):
        self.invocation_id = "inv-1"
        self.branch = "main"
        self.actions = EventActions()
        self.state = {}


def test_from_session_new_is_empty():
    state = DSHState.from_session(_Session())

    assert state.jsonl == ""
    assert state.event_count == 0
    assert state.version == 4


def test_append_event_requires_a_snapshot():
    state = DSHState.from_session(_Session())

    with pytest.raises(ValueError, match="snapshot is empty"):
        state.append_event({"type": "turn/start", "seq": 0, "time": 1, "data": {}})


def test_append_event_validates_contiguity():
    state = DSHState.from_jsonl(_jsonl(), 4)

    state.append_event({"type": "turn/start", "seq": 0, "time": 1, "data": {}})
    assert state.event_count == 1
    assert '"type":"turn/start"' in state.jsonl

    with pytest.raises(ValueError):
        state.append_event({"type": "turn/end", "seq": 2, "time": 2, "data": {}})


def test_append_event_ignores_duplicate_seq():
    state = DSHState.from_jsonl(_jsonl(), 4)
    event = {"type": "turn/start", "seq": 0, "time": 1, "data": {}}

    state.append_event(event)
    state.append_event(event)

    assert state.event_count == 1


def test_from_session_loads_existing_state_and_replace():
    state = DSHState.from_jsonl(
        _jsonl({"type": "turn/start", "seq": 0, "time": 1, "data": {}}),
        4,
    )

    session = _Session({
        DSH_SESSION_STATE_KEY: {
            "jsonl": state.jsonl,
            "version": state.version,
        },
    })
    loaded = DSHState.from_session(session)
    assert loaded.event_count == 1
    assert loaded.jsonl == state.jsonl

    loaded.replace(_jsonl(
        {"type": "turn/start", "seq": 0, "time": 1, "data": {}},
        {"type": "turn/end", "seq": 1, "time": 2, "data": {}},
    ), 4)
    assert loaded.event_count == 2


def test_from_jsonl_counts_events():
    jsonl = _jsonl(
        {"type": "turn/start", "seq": 0, "time": 1, "data": {}},
        {"type": "turn/end", "seq": 1, "time": 2, "data": {}},
    )

    state = DSHState.from_jsonl(jsonl, 4)
    assert state.event_count == 2
    assert state.jsonl == jsonl


def test_direct_construction_counts_events():
    jsonl = _jsonl(
        {"type": "turn/start", "seq": 0, "time": 1, "data": {}},
        {"type": "turn/end", "seq": 1, "time": 2, "data": {}},
    )

    state = DSHState(jsonl=jsonl)
    assert state.event_count == 2


def test_sync_run_input_fills_default_snapshot():
    state = DSHState.from_jsonl(_jsonl(), 4)
    run_input = DSHRunInput(
        session_id="s1",
        content_blocks=[{"type": "text", "text": "hi"}],
    )

    state.sync_run_input(run_input)

    assert run_input.jsonl == state.jsonl
    assert run_input.version == 4


def test_sync_run_input_adopts_callback_snapshot():
    state = DSHState.from_jsonl(_jsonl(), 4)
    new_jsonl = json.dumps({**_HEADER, "version": 5}) + "\n"
    run_input = DSHRunInput(
        session_id="s1",
        content_blocks=[{"type": "text", "text": "hi"}],
        jsonl=new_jsonl,
    )

    state.sync_run_input(run_input)

    assert state.jsonl == new_jsonl
    assert state.version == 5
    assert run_input.version == 5


def test_to_session_writes_state_and_returns_event():
    state = DSHState.from_jsonl(_jsonl(), 4)
    ctx = _Context()

    event = state.to_session(ctx, author="dsh_agent")

    assert ctx.state[DSH_SESSION_STATE_KEY] == {
        "jsonl": state.jsonl,
        "version": 4,
    }
    assert event.invocation_id == "inv-1"
    assert event.author == "dsh_agent"
    assert event.branch == "main"
    assert event.visible is False
    assert event.partial is False
