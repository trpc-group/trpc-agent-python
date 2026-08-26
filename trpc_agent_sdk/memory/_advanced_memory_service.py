# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Runner-compatible facade for the Advanced Memory mechanism."""

from __future__ import annotations

from typing import Any
from typing import Optional
from typing import TYPE_CHECKING

from trpc_agent_sdk.abc import MemoryServiceABC as BaseMemoryService
from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.abc import SearchMemoryResponse
from trpc_agent_sdk.abc import SessionServiceABC
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.sessions import Session

if TYPE_CHECKING:
    from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
    from trpc_agent_sdk.advanced_memory import AdvancedMemoryIntegration
    from trpc_agent_sdk.advanced_memory import AdvancedMemoryRuntime


class AdvancedMemoryService(BaseMemoryService):
    """Expose Advanced Memory through the standard Runner memory API.

    Advanced Memory is more than a traditional ``MemoryServiceABC``: it also
    installs agent callbacks and decorates the session service. ``Runner``
    calls :meth:`bind` automatically when this service is supplied as its
    ``memory_service``.
    """

    def __init__(
        self,
        config: AdvancedMemoryConfig | None = None,
        *,
        runtime: AdvancedMemoryRuntime | None = None,
        summary_generator: Any | None = None,
        session_memory_generator: Any | None = None,
        compact_model: Any | None = None,
        session_memory_model: Any | None = None,
        install_long_term_memory_tools: bool = True,
    ) -> None:
        """Create an Advanced Memory service without binding it to an agent."""
        from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
        from trpc_agent_sdk.advanced_memory import AdvancedMemoryRuntime

        if config is not None and runtime is not None and config != runtime.config:
            raise ValueError("config and runtime must describe the same Advanced Memory configuration")
        resolved_config = runtime.config if runtime is not None else (config or AdvancedMemoryConfig())
        super().__init__(MemoryServiceConfig(enabled=resolved_config.enabled))
        self._runtime = runtime or AdvancedMemoryRuntime.create(resolved_config)
        self._summary_generator = summary_generator
        self._session_memory_generator = session_memory_generator
        self._compact_model = compact_model
        self._session_memory_model = session_memory_model
        self._install_long_term_memory_tools = install_long_term_memory_tools
        self._integration: AdvancedMemoryIntegration | None = None
        self._bound_agent: Any | None = None
        self._bound_session_service: SessionServiceABC | None = None

    @property
    def config(self) -> AdvancedMemoryConfig:
        """Return the Advanced Memory configuration."""
        return self._runtime.config

    @property
    def runtime(self) -> AdvancedMemoryRuntime:
        """Return the Advanced Memory runtime."""
        return self._runtime

    @property
    def integration(self) -> AdvancedMemoryIntegration | None:
        """Return the binding result after the service is attached to a Runner."""
        return self._integration

    def bind(self, agent: Any, session_service: SessionServiceABC) -> SessionServiceABC:
        """Bind callbacks and tools, returning the wrapped session service."""
        from trpc_agent_sdk.advanced_memory import setup_advanced_memory

        if self._integration is not None:
            if agent is not self._bound_agent:
                raise ValueError("AdvancedMemoryService is already bound to another agent")
            if session_service is not self._bound_session_service:
                raise ValueError("AdvancedMemoryService is already bound to another session service")
            return self._integration.session_service

        self._integration = setup_advanced_memory(
            agent,
            session_service,
            self._runtime,
            self._summary_generator,
            self._session_memory_generator,
            compact_model=self._compact_model,
            session_memory_model=self._session_memory_model,
            install_long_term_memory_tools=self._install_long_term_memory_tools,
        )
        self._bound_agent = agent
        self._bound_session_service = session_service
        return self._integration.session_service

    async def store_session(
        self,
        session: Session,
        agent_context: Optional[AgentContext] = None,
    ) -> None:
        """Keep the standard Runner post-turn contract without duplicating work.

        The wrapped session service performs session-memory extraction from
        ``create_session_summary`` before Runner reaches this method.
        """
        return None

    async def search_memory(
        self,
        key: str,
        query: str,
        limit: int = 10,
        agent_context: Optional[AgentContext] = None,
    ) -> SearchMemoryResponse:
        """Return an empty legacy-style response.

        Advanced long-term memory is intentionally accessed through its
        ``save_memory``, ``read_memory``, and ``list_memory_index`` tools.
        """
        return SearchMemoryResponse()

    async def close(self) -> None:
        """Release service-owned resources.

        Advanced Memory stores are file-backed and do not own an external
        connection. The wrapped session service is closed by Runner.
        """
        return None
