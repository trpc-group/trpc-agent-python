# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Standalone Agent integration for the Advanced Memory mechanism."""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.sessions.compact._callbacks import install_staged_callback

from ._advanced_memory_preload import AdvancedMemoryPreloader
from ._advanced_memory_preload import AdvancedModelMemoryRelevanceSelector
from ._config import AdvancedMemoryConfig
from ._memory_context import LongTermMemoryContext
from ._memory_context import LongTermMemoryContextCallback
from ._runtime import AdvancedMemoryRuntime


class AdvancedMemory:
    """Manage Advanced Memory storage and Agent integration.

    Application code explicitly creates tools and calls :meth:`configure_agent`.
    The component is configured explicitly by the application; long-term memory
    is written and read by the Agent through explicit tools.
    """

    def __init__(
        self,
        config: AdvancedMemoryConfig | None = None,
        *,
        runtime: AdvancedMemoryRuntime | None = None,
        preload_memory_model: Any | None = None,
    ) -> None:
        """Create Advanced Memory without configuring an Agent."""
        if config is not None and runtime is not None and config != runtime.config:
            raise ValueError("config and runtime must describe the same Advanced Memory configuration")
        resolved_config = runtime.config if runtime is not None else (config or AdvancedMemoryConfig())
        self._runtime = runtime or AdvancedMemoryRuntime.create(resolved_config)
        self._preload_memory_model = preload_memory_model
        self._configured_agent: Any | None = None

    @property
    def config(self) -> AdvancedMemoryConfig:
        """Return the Advanced Memory configuration."""
        return self._runtime.config

    @property
    def runtime(self) -> AdvancedMemoryRuntime:
        """Return the Advanced Memory runtime."""
        return self._runtime

    def create_toolset(self) -> Any:
        """Create the toolset to add to an Agent."""
        from trpc_agent_sdk.tools import create_advanced_memory_toolset

        return create_advanced_memory_toolset(self._runtime)

    def create_preload_tool(self) -> Any | None:
        """Create the optional preload tool for explicit Agent registration."""
        if (not self.config.enabled or not self.config.preload_memory_enabled):
            return None
        from trpc_agent_sdk.tools import PreloadMemoryTool

        preloader = AdvancedMemoryPreloader(
            self._runtime,
            AdvancedModelMemoryRelevanceSelector(self._preload_memory_model),
        )
        return PreloadMemoryTool(
            memory_preloader=preloader.preload,
            use_legacy_memory=False,
        )

    def configure_agent(self, agent: Any) -> None:
        """Configure the Agent with the long-term memory index callback."""
        if self._configured_agent is not None:
            if agent is not self._configured_agent:
                raise ValueError("AdvancedMemory is already configured for another agent")
            return

        memory_context = LongTermMemoryContext(self._runtime)
        install_staged_callback(
            agent,
            LongTermMemoryContextCallback(memory_context),
            callback_type=LongTermMemoryContextCallback,
            component_attribute="memory_context",
            memory_runtime=self._runtime,
            conflict_message="Long-term memory context is already configured with another runtime",
        )
        self._configured_agent = agent

    async def close(self) -> None:
        """Release the Runtime's local or external storage resources."""
        await self._runtime.close()
