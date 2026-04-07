# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""trpc_claw session memory utils."""

import contextvars
from typing import Optional

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.sessions import Session


def get_memory_key(session: Session) -> str:
    """Get the memory key for a session.

    Args:
        session: The session.

    Returns:
        str: The memory key.
    """
    return f"{session.app_name}/{session.user_id}/{session.id}"


def make_memory_key(app_name: str, user_id: str, session_id: str) -> str:
    """Get the memory key from a app name, user id and session id.

    Args:
        app_name: The app name.
        user_id: The user ID.
        session_id: The session ID.

    Returns:
        str: The memory key.
    """
    return f"{app_name}/{user_id}/{session_id}"


def get_memory_key_from_save_key(save_key: str) -> str:
    """Get the memory key from a save key.

    Args:
        save_key: The save key.

    Returns:
        str: The memory key.
    """
    return save_key.replace(":", "_")


def get_memory_key_from_session(session: Session) -> str:
    """Get the memory key from a session.

    Args:
        session: The session.

    Returns:
        str: The memory key.
    """
    return f"{session.app_name}/{session.user_id}/{session.id}"


_agent_context = contextvars.ContextVar("agent_context", default=None)


def get_agent_context() -> Optional[AgentContext]:
    return _agent_context.get()


def set_agent_context(agent_context: AgentContext) -> None:
    _agent_context.set(agent_context)
