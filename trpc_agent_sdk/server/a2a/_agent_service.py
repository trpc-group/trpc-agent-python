# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""A2A service that uses unprefixed metadata and artifact-first streaming.

This service provides a bridge between trpc-agent and the A2A protocol, allowing
users to easily deploy trpc-agent as an A2A service. It extends ``AgentExecutor``
from the A2A SDK so it can be used directly with ``A2AStarletteApplication`` or
any other A2A-compatible server.
"""

from __future__ import annotations

import asyncio
from typing import Optional
from typing_extensions import override

from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import AgentCard

from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.memory import BaseMemoryService
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import BaseSessionService
from trpc_agent_sdk.sessions import InMemorySessionService

from ._agent_card_builder import AgentCardBuilder
from .executor import TrpcA2aAgentExecutor
from .executor import TrpcA2aAgentExecutorConfig


class TrpcA2aAgentService(AgentExecutor):
    """A2A service that integrates trpc-agent with the standard A2A SDK (adk-python).

    This service provides a bridge between trpc-agent and the A2A protocol using
    unprefixed metadata keys and artifact-first streaming. It extends ``AgentExecutor``
    from the A2A SDK so it can be used directly with ``A2AStarletteApplication``.

    Attributes:
        agent: The trpc-agent BaseAgent to use (required).
        agent_card: The A2A AgentCard metadata. Auto-built if not provided.
        executor_config: Configuration for the TrpcA2aAgentExecutor.
    """

    def __init__(
        self,
        *,
        service_name: str,
        agent: BaseAgent,
        agent_card: Optional[AgentCard] = None,
        session_service: Optional[BaseSessionService] = None,
        memory_service: Optional[BaseMemoryService] = None,
        executor_config: Optional[TrpcA2aAgentExecutorConfig] = None,
    ):
        super().__init__()
        self._agent = agent
        self._agent_card = agent_card
        self._service_name = service_name
        self._session_service = session_service
        self._memory_service = memory_service
        self._executor_config = executor_config

    def initialize(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        loop.run_until_complete(self._initialize())

    @property
    def agent_card(self) -> Optional[AgentCard]:
        """Return the agent card metadata.

        Returns:
            AgentCard: The agent's metadata describing its capabilities
        """
        return self._agent_card

    async def _initialize(self) -> None:
        """Initialize Resources"""
        if self._session_service is None:
            self._session_service = InMemorySessionService()

        if self._agent_card is None:
            builder = AgentCardBuilder(agent=self._agent)
            self._agent_card = await builder.build()

        self._agent_card.capabilities.streaming = True
        logger.info("Initialized A2A Agent Service %s for %s", self._service_name, self._agent.name)

    def _create_executor(self) -> TrpcA2aAgentExecutor:
        runner = Runner(
            app_name=self._service_name,
            agent=self._agent,
            session_service=self._session_service,
            memory_service=self._memory_service,
        )
        config = self._executor_config or TrpcA2aAgentExecutorConfig()
        return TrpcA2aAgentExecutor(runner=runner, config=config)

    async def get_agent_card(self) -> AgentCard:
        """Return the agent card metadata.

        Returns:
            AgentCard: The agent's metadata describing its capabilities
        """
        return self._agent_card

    @override
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Execute the agent's logic for a given A2A request context.

        Args:
            context: A2A RequestContext containing the message and task information
            event_queue: Queue to publish events to
        """
        executor = self._create_executor()
        await executor.execute(context, event_queue)

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel an ongoing task.

        Args:
            context: A2A RequestContext containing task information
            event_queue: Queue to publish cancellation status to
        """
        executor = self._create_executor()
        await executor.cancel(context, event_queue)
