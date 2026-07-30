# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Core context management for TRPC Agent invocations.

This module defines the InvocationContext class which serves as the central container
for all contextual information during agent execution. Key responsibilities include:

- Managing agent invocation lifecycle
- Providing access to core services (session, artifact, memory)
- Maintaining invocation state and configuration
- Handling event actions and streaming tools

The InvocationContext is designed to be:
- Immutable for core attributes (enforced via Pydantic)
- Extensible for custom services
- Thread-safe for concurrent operations

Example Usage:
    ctx = InvocationContext(
        session_service=session_service,
        invocation_id=new_invocation_context_id(),
        agent=agent,
        session=session
    )
    await agent.run_async(ctx)
"""

from __future__ import annotations

import asyncio
import uuid
from types import MappingProxyType
from typing import Any
from typing import List
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from trpc_agent_sdk.abc import AgentABC
from trpc_agent_sdk.abc import ArtifactEntry
from trpc_agent_sdk.abc import ArtifactId
from trpc_agent_sdk.abc import ArtifactServiceABC
from trpc_agent_sdk.abc import MemoryServiceABC
from trpc_agent_sdk.abc import SessionABC
from trpc_agent_sdk.abc import SessionServiceABC
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.types import ActiveStreamingTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import SearchMemoryResponse
from trpc_agent_sdk.types import State
from trpc_agent_sdk.utils import user_key

from ._agent_context import AgentContext


class InvocationContext(BaseModel):
    """An agent context represents the data of a single invocation of an agent.

    An invocation:
        1. Starts with a user message and ends with a final response.
        2. Can contain one or multiple agent calls.
        3. Is handled by runner.run_async().

    An invocation runs an agent until it does not request to transfer to another
    agent.

    An agent call:
        1. Is handled by agent.run().
        2. Ends when agent.run() ends.

    The agent context provides access to all services and data needed during
    agent execution, including session management, artifact storage, memory,
    and configuration.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
    )
    """The pydantic model config."""

    # Required services
    session_service: SessionServiceABC
    """The session service for managing conversation sessions."""

    # Optional services
    artifact_service: Optional[ArtifactServiceABC] = None
    """The artifact service for storing and retrieving files."""

    memory_service: Optional[MemoryServiceABC] = None
    """The memory service for storing and searching conversation history."""

    # Core context data
    invocation_id: str
    """The id of this invocation context. Readonly."""

    branch: Optional[str] = None
    """The branch of the invocation context.

    The format is like agent_1.agent_2.agent_3, where agent_1 is the parent of
    agent_2, and agent_2 is the parent of agent_3.

    Branch is used when multiple sub-agents shouldn't see their peer agents'
    conversation history.
    """

    agent: AgentABC
    """The current agent of this invocation context. Readonly."""

    agent_context: AgentContext
    """The agent context for user interaction control."""

    user_content: Optional[Content] = None
    """The user content that started this invocation. Readonly."""

    session: SessionABC
    """The current session of this invocation context. Readonly."""

    # Control flags
    end_invocation: bool = False
    """Whether to end this invocation.

    Set to True in callbacks or tools to terminate this invocation."""

    # Configuration
    run_config: Optional[RunConfig] = None
    """Configuration for agent execution under this invocation."""

    event_actions: EventActions = Field(default=EventActions(), init=True)

    callback_state: Optional[State] = None

    active_streaming_tools: Optional[dict[str, ActiveStreamingTool]] = None
    """The running streaming tools of this invocation."""

    function_call_id: Optional[str] = None
    """The ID of the current function call."""

    override_messages: Optional[List[Content]] = None
    """Optional pre-built messages to use instead of session history.

    When provided, LlmAgent will use these messages as conversation context
    instead of building history from session.events. This enables TeamAgent
    to control member agent context.

    This field is typically set by TeamAgent when executing member agents
    with controlled message history.
    """

    session_key: Optional[Any] = None
    """Session key for cancellation tracking. Set by Runner.

    This field stores the SessionKey used to track cancellation state
    for the current run. It is set by Runner.run_async() and used by
    agents to check for cancellation at checkpoints.
    """

    @property
    def app_name(self) -> str:
        """Get the application name from the session."""
        return self.session.app_name

    @property
    def user_id(self) -> str:
        """Get the user ID from the session."""
        return self.session.user_id

    @property
    def session_id(self) -> str:
        """Get the session ID from the session."""
        return self.session.id

    @property
    def agent_name(self) -> str:
        """Gets the name of the currently executing agent.

        Returns:
            The name identifier of the agent that is currently processing
            this invocation.
        """
        return self.agent.name

    @property
    def session_state(self) -> MappingProxyType[str, Any]:
        """Gets an immutable view of the current session state.

        Returns:
            A read-only mapping proxy of the current session state dictionary.
            This prevents accidental modification of the session state while
            allowing read access to state values.

        Note:
            The returned MappingProxyType ensures any attempt to modify the
            state through this property will raise an AttributeError.
        """
        return MappingProxyType(self.session.state)

    @property
    def actions(self) -> EventActions:
        return self.event_actions

    @property
    def state(self) -> State:
        """Get the delta-aware state for this invocation context.

        Returns:
            State: Mutable state object that tracks changes in delta
        """
        if self.callback_state is None:
            self.callback_state = State(value=self.session.state, delta=self.event_actions.state_delta)
        return self.callback_state

    async def load_artifact(self, filename: str, version: Optional[int] = None) -> Optional[ArtifactEntry]:
        """Loads an artifact attached to the current session.

        Args:
          filename: The filename of the artifact.
          version: The version of the artifact. If None, the latest version will be
            returned.

        Returns:
          The artifact.
        """
        if self.artifact_service is None:
            raise ValueError("Artifact service is not initialized.")
        return await self.artifact_service.load_artifact(
            artifact_id=ArtifactId(
                app_name=self.app_name,
                user_id=self.user_id,
                session_id=self.session.id,
                filename=filename,
            ),
            version=version,
        )

    async def save_artifact(self, filename: str, artifact: Part) -> int:
        """Saves an artifact and records it as delta for the current session.

        Args:
          filename: The filename of the artifact.
          artifact: The artifact to save.

        Returns:
         The version of the artifact.
        """
        if self.artifact_service is None:
            raise ValueError("Artifact service is not initialized.")
        version = await self.artifact_service.save_artifact(
            artifact_id=ArtifactId(
                app_name=self.app_name,
                user_id=self.user_id,
                session_id=self.session.id,
                filename=filename,
            ),
            artifact=artifact,
        )
        self.event_actions.artifact_delta[filename] = version
        return version

    async def list_artifacts(self) -> list[str]:
        """List all artifact filenames associated with the current session.

        Returns:
            list[str]: Names of available artifacts

        Raises:
            ValueError: If artifact service is not initialized

        Behavior:
            - Queries artifact service using current session context
            - Returns empty list if no artifacts exist
        """
        if self.artifact_service is None:
            raise ValueError('Artifact service is not initialized.')
        return await self.artifact_service.list_artifact_keys(artifact_id=ArtifactId(
            app_name=self.app_name,
            user_id=self.user_id,
            session_id=self.session.id,
        ), )

    async def search_memory(self, query: str) -> SearchMemoryResponse:
        """Search user's memory using the provided query string.

        Args:
            query: Search string to match against stored memories

        Returns:
            SearchMemoryResponse: Structured search results

        Raises:
            ValueError: If memory service is not available

        Notes:
            - Results are scoped to current user and application
            - Supports semantic search capabilities
        """
        if self.memory_service is None:
            raise ValueError('Memory service is not available.')
        return await self.memory_service.search_memory(
            key=user_key(self.app_name, self.user_id),
            query=query,
            agent_context=self.agent_context,
        )

    async def raise_if_cancelled(self) -> None:
        """Raise RunCancelledException if this run is cancelled.

        This is a convenience method for checkpoint checks.

        Raises:
            RunCancelledException: If cancelled.
        """
        if self.session_key is None:
            return
        from trpc_agent_sdk.cancel import raise_if_cancelled
        await raise_if_cancelled(self.session_key)

    async def get_cancel_event(self) -> Optional[asyncio.Event]:
        """Get the cancellation event for this invocation.

        This returns the asyncio.Event that will be set when cancellation
        is requested for the current run. Useful for concurrent cancel
        detection in streaming scenarios.

        Returns:
            The asyncio.Event for cancellation, or None if cancel is not requested.
        """
        if self.session_key is None:
            return None
        from trpc_agent_sdk.cancel import get_cancel_event
        return await get_cancel_event(self.session_key)


def new_invocation_context_id() -> str:
    """Generate a new unique invocation context ID.

    Returns:
        str: A new unique invocation context ID with 'e-' prefix
    """
    return "e-" + str(uuid.uuid4())
