# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Session utilities."""

import copy
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Optional

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
