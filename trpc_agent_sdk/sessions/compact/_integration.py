# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Provide setup entry points for the context-compression pipeline."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from typing import TYPE_CHECKING

from ._autocompact import LegacySummaryGenerator
from ._autocompact import setup_autocompact
from ._history_snip import setup_history_snip
from ._microcompact import setup_microcompact
from ._runtime import AdvancedMemoryRuntime
from ._config import AdvancedCompactConfig
from ._manager import AdvancedSessionCompactManager
from ._session_memory import SessionMemoryExtractor
from ._session_memory import SessionMemoryGenerator
from ._tool_result_budget import setup_tool_result_budget

if TYPE_CHECKING:
    from trpc_agent_sdk.agents import LlmAgent
    from trpc_agent_sdk.sessions import SessionServiceABC


def setup_context_compression(
    agent: "LlmAgent",
    session_service: "SessionServiceABC",
    memory_runtime: AdvancedMemoryRuntime,
    summary_generator: LegacySummaryGenerator | None = None,
    *,
    compact_model: Any | None = None,
    session_memory_generator: SessionMemoryGenerator | None = None,
    session_memory_model: Any | None = None,
) -> "SessionServiceABC":
    """Install native Session compression on an existing SessionService.

    The original service remains responsible for persistence. Session Compact
    is attached through the BaseSessionService manager lifecycle.
    """
    session_config = getattr(session_service, "session_config", None)
    if session_config is None or not getattr(session_config, "store_historical_events", False):
        raise ValueError(
            "Context compression requires "
            "SessionServiceConfig(store_historical_events=True)"
        )
    if getattr(session_service, "summarizer_manager", None) is not None:
        raise ValueError(
            "Context compression and SummarizerSessionManager are mutually exclusive"
        )

    manager = getattr(session_service, "session_compact_manager", None)
    if manager is not None:
        if not isinstance(manager, AdvancedSessionCompactManager):
            raise ValueError(
                "Advanced context compression requires an "
                "AdvancedSessionCompactManager"
            )
        if manager.runtime is not memory_runtime:
            raise ValueError("Context compression session service uses another runtime")
        extractor = manager.session_memory_extractor
        if session_memory_generator is not None or session_memory_model is not None:
            raise ValueError(
                "Session Memory extractor is already configured; "
                "do not provide another generator or model"
            )
    else:
        attach_manager = getattr(session_service, "set_session_compact_manager", None)
        if not callable(attach_manager):
            raise TypeError(
                "Context compression requires a BaseSessionService with "
                "set_session_compact_manager()"
            )
        extractor = SessionMemoryExtractor(
            memory_runtime,
            session_memory_generator,
            model=session_memory_model,
        )
        manager = AdvancedSessionCompactManager(
            memory_runtime,
            extractor,
        )
        attach_manager(manager)
    setup_tool_result_budget(agent, memory_runtime)
    setup_history_snip(agent, memory_runtime)
    setup_microcompact(agent, memory_runtime)
    autocompact = setup_autocompact(
        agent,
        memory_runtime,
        summary_generator,
        model=compact_model,
    )
    autocompact.attach_session_memory_extractor(extractor)
    return session_service


def setup_advanced_session_compact(
    agent: Any,
    session_service: "SessionServiceABC",
    compact_config: AdvancedCompactConfig,
    *,
    summary_generator: LegacySummaryGenerator | None = None,
    compact_model: Any | None = None,
    session_memory_generator: SessionMemoryGenerator | None = None,
    session_memory_model: Any | None = None,
) -> AdvancedSessionCompactManager:
    """Configure Advanced Compact from a standard SessionService backend."""
    from trpc_agent_sdk.sessions import InMemorySessionService
    from trpc_agent_sdk.sessions import RedisSessionService
    from trpc_agent_sdk.sessions import SqlSessionService

    if isinstance(session_service, RedisSessionService):
        resolved_config = replace(
            compact_config,
            storage_backend="redis",
            redis_url=session_service.db_url,
            redis_is_async=session_service.is_async,
        )
    elif isinstance(session_service, SqlSessionService):
        resolved_config = replace(
            compact_config,
            storage_backend="sql",
            sql_url=session_service.db_url,
            sql_is_async=session_service.is_async,
        )
    elif isinstance(session_service, InMemorySessionService):
        resolved_config = replace(compact_config, storage_backend="local")
    else:
        raise TypeError(
            "Advanced Compact supports InMemorySessionService, "
            "RedisSessionService, and SqlSessionService"
        )
    runtime = AdvancedMemoryRuntime.create(resolved_config)
    extractor = SessionMemoryExtractor(
        runtime,
        session_memory_generator,
        model=session_memory_model,
    )
    manager = AdvancedSessionCompactManager(runtime, extractor)
    setup_tool_result_budget(agent, runtime)
    setup_history_snip(agent, runtime)
    setup_microcompact(agent, runtime)
    autocompact = setup_autocompact(
        agent,
        runtime,
        summary_generator,
        model=compact_model,
    )
    autocompact.attach_session_memory_extractor(extractor)
    session_service.set_session_compact_manager(manager)
    return manager
