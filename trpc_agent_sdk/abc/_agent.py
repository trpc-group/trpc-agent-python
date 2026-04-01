# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""TRPC Agent Base Class Module.

This module defines the AgentABC class which serves as the foundation for all
agent implementations in the TRPC Agent Development Kit.

Key Features:
    - Core agent lifecycle management
    - Filter pipeline execution
    - Context propagation
    - Sub-agent hierarchy management
    - Callback handling (before/after execution)
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any
from typing import AsyncGenerator
from typing import Optional
from typing import TYPE_CHECKING
from typing_extensions import override

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from ._filter import FilterABC

if TYPE_CHECKING:
    from trpc_agent_sdk.context import InvocationContext


class AgentABC(BaseModel):
    """Base class for all agents in Agent Development Kit.

    Provides core functionality for agent execution including:
    - Filter management and execution
    - Asynchronous operation handling
    - Context management
    - Agent hierarchy management

    Attributes:
        name: The agent's name, must be a Python identifier and unique within the agent tree
        description: Description about the agent's capability
        parent_agent: The parent agent of this agent
        sub_agents: The sub-agents of this agent
        filters_name: List of filter names that will be applied during agent execution
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
    )
    """The pydantic model config."""

    name: str
    """The agent's name.

    Agent name must be a Python identifier and unique within the agent tree.
    Agent name cannot be "user", since it's reserved for end-user's input.
    """

    description: str = ""
    """Description about the agent's capability.

    The model uses this to determine whether to delegate control to the agent.
    One-line description is enough and preferred.
    """

    parent_agent: Optional[AgentABC] = Field(default=None, init=False)
    """The parent agent of this agent.

    Note that an agent can ONLY be added as sub-agent once.

    If you want to add one agent twice as sub-agent, consider to create two agent
    instances with identical config, but with different name and add them to the
    agent tree.
    """

    sub_agents: list[AgentABC] = Field(default_factory=list)
    """The sub-agents of this agent."""

    filters_name: list[str] = Field(default_factory=list)
    """List of filter names that will be applied during agent execution."""

    filters: list[FilterABC] = Field(default_factory=list)
    """List of filter instances that will be applied during agent execution."""

    disallow_transfer_to_parent: bool = False
    """Disallow transferring control to parent agent."""

    disallow_transfer_to_peers: bool = False
    """Disallow transferring control to peer agents."""

    @property
    def root_agent(self) -> AgentABC:
        """Gets the root agent of this agent."""
        root_agent = self
        while root_agent.parent_agent is not None:
            root_agent = root_agent.parent_agent
        return root_agent

    def get_agent_class(self) -> type[AgentABC]:
        """Return the runtime class of this agent instance.

        Examples:
            - AgentABC subclass instance -> that concrete subclass type
            - LlmAgent instance -> LlmAgent
        """
        return self.__class__

    def get_agent_type_name(self) -> str:
        """Return the runtime class name of this agent instance."""
        return self.get_agent_class().__name__

    @abstractmethod
    def get_subagents(self) -> list[AgentABC]:
        """Return the list of agents used as direct children for lookup.

        Subclasses must implement. Used by find_sub_agent (or overridden logic)
        when resolving agent by name.

        Returns:
            List of agents considered as direct children for lookup.
        """
        ...

    def find_agent(self, name: str) -> Optional[AgentABC]:
        """Finds the agent with the given name in this agent and its descendants.

        Args:
          name: The name of the agent to find.

        Returns:
          The agent with the matching name, or None if no such agent is found.
        """
        if self.name == name:
            return self
        return self.find_sub_agent(name)

    def find_sub_agent(self, name: str) -> Optional[AgentABC]:
        """Finds the agent with the given name in this agent's descendants.

        Args:
          name: The name of the agent to find.

        Returns:
          The agent with the matching name, or None if no such agent is found.
        """
        for sub_agent in self.sub_agents:
            if result := sub_agent.find_agent(name):
                return result
        return None

    @override
    def model_post_init(self, __context: Any) -> None:
        """Sets the parent agent for all sub-agents."""
        self.__set_parent_agent_for_sub_agents()

    def __set_parent_agent_for_sub_agents(self) -> AgentABC:
        """Sets the parent agent for all sub-agents."""
        for sub_agent in self.sub_agents:
            if sub_agent.parent_agent is not None:
                raise ValueError(f"Agent `{sub_agent.name}` already has a parent agent, current"
                                 f" parent: `{sub_agent.parent_agent.name}`, trying to add:"
                                 f" `{self.name}`")
            sub_agent.parent_agent = self
        return self

    @abstractmethod
    async def run_async(
        self,
        parent_context: InvocationContext,
    ) -> AsyncGenerator[Any, None]:
        """Entry point for agent execution.

        Args:
            parent_context: The parent context of the agent.

        Returns:
            An async generator of events.
        """
