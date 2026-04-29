# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Session utilities."""

import copy
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Optional

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.types import State


@dataclass
class StateStorageEntry:
    """The state delta."""
    app_state_delta: dict[str, Any] = field(default_factory=dict)
    user_state_delta: dict[str, Any] = field(default_factory=dict)
    session_state: dict[str, Any] = field(default_factory=dict)


def extract_state_delta(state_delta: Optional[dict[str, Any]], ignore_temp: bool = True) -> StateStorageEntry:
    """Extract state changes into app, user, and session state.

    Args:
        state_delta: State dictionary with potentially prefixed keys
        ignore_temp: Whether to ignore temporary state
    Returns:
        StateStorageEntry
    """
    app_state_delta = {}
    user_state_delta = {}
    session_state = {}
    if not state_delta:
        return StateStorageEntry(app_state_delta=app_state_delta,
                                 user_state_delta=user_state_delta,
                                 session_state=session_state)
    for key, value in state_delta.items():
        if key.startswith(State.APP_PREFIX):
            # Remove prefix for app state storage
            app_state_delta[key.removeprefix(State.APP_PREFIX)] = value
        elif key.startswith(State.USER_PREFIX):
            # Remove prefix for user state storage
            user_state_delta[key.removeprefix(State.USER_PREFIX)] = value
        elif ignore_temp and key.startswith(State.TEMP_PREFIX):
            # Skip temporary state - never persisted
            continue
        else:
            # Session-scoped state
            session_state[key] = value
    return StateStorageEntry(app_state_delta=app_state_delta,
                             user_state_delta=user_state_delta,
                             session_state=session_state)


def merge_state(state_delta: StateStorageEntry, need_copy: bool = True) -> dict[str, Any]:
    """Merge app state, user state, and session state into a single state dictionary.

    Args:
        state_delta: StateStorageEntry
        need_copy: Whether to copy the session state

    Returns:
        Merged state dictionary with prefixed keys for app and user state in StateStorageEntry
    """
    if need_copy:
        merged_state = copy.deepcopy(state_delta.session_state)
    else:
        merged_state = state_delta.session_state
    for key in state_delta.app_state_delta.keys():
        merged_state[State.APP_PREFIX + key] = state_delta.app_state_delta[key]
    for key in state_delta.user_state_delta.keys():
        merged_state[State.USER_PREFIX + key] = state_delta.user_state_delta[key]
    return merged_state


def is_summary_anchor(event: Event) -> bool:
    """Return whether the event can anchor a summary window."""
    return (event.author and event.author.lower() == "user") or event.is_summary_event()


def find_events_for_summary(events: list[Event],
                            keep_recent_count: int = 10,
                            start_by_user_turn: bool = True) -> tuple[list[Event], int]:
    """Find events that should be summarized.

    Args:
        events: Source events to inspect.
        keep_recent_count: Number of recent model-visible events to keep out of the summary window.
        start_by_user_turn: Whether to align the summary window to user or summary events.

    Returns:
        A tuple of selected events and the summary insertion index in the original events.
        Returns ([], -1) when no model-visible events can be selected.
    """
    visible_event_indices = [idx for idx, event in enumerate(events) if event.is_model_visible()]
    if not visible_event_indices:
        return [], -1

    first_visible_index = visible_event_indices[0]
    last_visible_index = visible_event_indices[-1]
    start_index = first_visible_index

    if start_by_user_turn and not is_summary_anchor(events[start_index]):
        for idx in range(first_visible_index - 1, -1, -1):
            if is_summary_anchor(events[idx]):
                start_index = idx
                break

    window_end_index = last_visible_index + 1
    visible_events_count = len(visible_event_indices)
    if keep_recent_count <= 0 or keep_recent_count >= visible_events_count:
        insert_index = window_end_index
    else:
        insert_index = visible_event_indices[-keep_recent_count]
        if start_by_user_turn:
            for idx in range(insert_index, window_end_index):
                if is_summary_anchor(events[idx]):
                    insert_index = idx
                    break

    selected_events = events[start_index:insert_index]
    if not selected_events:
        return [], -1

    return selected_events, insert_index


def session_key(app_name: str, user_id: str, session_id: str) -> str:
    """Generate a key for a session.

    Args:
        app_name: Application name
        user_id: User identifier
        session_id: Session identifier

    Returns:
        Formatted session key string
    """
    return f"session:{app_name}:{user_id}:{session_id}"


def app_state_key(app_name: str) -> str:
    """Generate a key for app state.

    Args:
        app_name: Application name

    Returns:
        Formatted app state key string
    """
    return f"app_state:{app_name}"


def user_state_key(app_name: str, user_id: str) -> str:
    """Generate a key for user state.

    Args:
        app_name: Application name
        user_id: User identifier

    Returns:
        Formatted user state key string
    """
    return f"user_state:{app_name}:{user_id}"
