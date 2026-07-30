# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Transfer Agent Implementation.

Provides TransferAgent class for custom agents that lack transfer capabilities.
"""

from __future__ import annotations

from typing import Any
from typing import AsyncGenerator
from typing import List
from typing import Optional
from typing import Union
from typing_extensions import override

from trpc_agent_sdk.abc import AgentABC
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel

from ._base_agent import BaseAgent
from ._llm_agent import LlmAgent

TRPC_TRANSFER_AGENT_RESULT_KEY = "trpc_transfer_agent_result"
TRPC_TRANSFER_INSTRUCTION_KEY = "trpc_transfer_instruction"
TRPC_TRANSFER_AGENT_LIST_KEY = "trpc_transfer_agent_list"

# Default transfer instruction when not provided
_DEFAULT_TRANSFER_INSTRUCTION = """1. If the content indicates that the task requires specialized expertise that matches
   a sub_agent's description, transfer to that sub_agent
2. If the content contains errors, incomplete information, or requests for additional help,
   consider transferring to an appropriate sub_agent
3. If the content is complete and satisfactory, do not transfer
4. Choose the most appropriate sub_agent based on their description and the content's needs

Return should_transfer=true only if there is a clear match between the content's needs
and a sub_agent's capabilities."""

_DECISION_INSTRUCTION = """Based on the following target agent result and the rules below,
decide whether to transfer to a sub-agent.

- If yes: call the transfer_to_agent tool with the target agent name only. Do not output other text.
- If no: do not call any tool and do not output any content (no "should_transfer=...", no JSON, no explanation).

Target agent result:
{trpc_transfer_agent_result}

Rules:
{trpc_transfer_instruction}

Available sub-agents:
{trpc_transfer_agent_list}"""


class TransferAgent(BaseAgent):
    """Transfer proxy for custom agents that lack transfer capabilities.

    Always calls target agent first, then optionally transfers to sub-agents based on
    transfer instructions. Agent name is auto-generated as "transfer_{target_agent.name}".

    Behavior:
    - No sub_agents: Directly transfer to target agent
    - Has sub_agents: Call target agent, analyze result, decide transfer
    """

    _target_agent: BaseAgent
    """The target agent that TransferAgent will always call first."""

    _model: Union[str, LLMModel, Any]
    """The model to use for transfer decision."""

    _transfer_instruction: str
    """Transfer instructions for deciding whether to transfer to sub-agents."""

    _sub_agents: List[AgentABC]
    """Sub-agents that can be transferred to after target agent execution."""

    @property
    def target_agent(self) -> BaseAgent:
        """Target agent."""
        return self._target_agent

    @override
    def get_subagents(self) -> List[AgentABC]:
        return [self._target_agent] + self._sub_agents

    @override
    def find_sub_agent(self, name: str) -> Optional[AgentABC]:
        """Use get_subagents() so find_agent can resolve transfer targets."""
        for sub_agent in self.get_subagents():
            if result := sub_agent.find_agent(name):
                return result
        return None

    def __init__(
        self,
        agent: BaseAgent,
        model: Union[str, LLMModel, Any],
        sub_agents: Optional[List[AgentABC]] = None,
        transfer_instruction: str = "",
    ) -> None:
        """Initialize TransferAgent.

        Args:
            agent: Target agent (required).
            model: Model for transfer decision (required).
            sub_agents: Optional sub-agents to transfer to. If empty, directly transfer to target agent.
            transfer_instruction: Custom transfer rules. Uses default if empty.
        """
        final_sub_agents = [a for a in (sub_agents or []) if a != agent]
        if not transfer_instruction.strip():
            transfer_instruction = _DEFAULT_TRANSFER_INSTRUCTION

        super().__init__(
            name=f"{agent.name}_transfer_proxy",
            description=f"Transfer proxy for {agent.name}",
        )

        self._target_agent = agent
        self._model = model
        self._transfer_instruction = transfer_instruction
        self._sub_agents = final_sub_agents
        self._route_agent = LlmAgent(
            name=f"{agent.name}_transfer",
            description="Internal agent for transfer decision",
            model=model,
            instruction=_DECISION_INSTRUCTION,
            sub_agents=final_sub_agents,
        )
        self._route_agent.parent_agent = self.parent_agent

    @override
    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Execute TransferAgent.

        If no sub_agents: directly transfer to target agent.
        If has sub_agents: call target agent, analyze result, decide transfer.
        """
        result_text = ""
        async for event in self._target_agent.run_async(ctx):
            if event.actions and event.actions.state_delta:
                ctx.state.update(event.actions.state_delta)
            if not event.partial and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        result_text += part.text + "\n"
            yield event

        ctx.state[TRPC_TRANSFER_AGENT_RESULT_KEY] = result_text.strip() or "(no content)"
        ctx.state[TRPC_TRANSFER_INSTRUCTION_KEY] = (self._transfer_instruction.strip() or _DEFAULT_TRANSFER_INSTRUCTION)
        ctx.state[TRPC_TRANSFER_AGENT_LIST_KEY] = ("\n".join(f"- {a.name}: {a.description}"
                                                             for a in self._sub_agents) if self._sub_agents else "None")

        async for event in self._route_agent.run_async(ctx):
            yield event
