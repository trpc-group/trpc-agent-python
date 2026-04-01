# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Agent Transfer Processor implementation for TRPC Agent framework.

This module provides the AgentTransferProcessor class which handles agent transfer
functionality. It identifies available transfer targets and adds appropriate
instructions and tools to enable LLM-controlled agent transfers.
"""

from __future__ import annotations

from typing import List
from typing import Optional
from typing import TYPE_CHECKING

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest

from .._base_agent import BaseAgent
if TYPE_CHECKING:
    from .._llm_agent import LlmAgent


class AgentTransferProcessor:
    """Processor for handling agent transfer functionality.

    This class manages the addition of transfer capabilities to LLM requests,
    including identifying transfer targets and adding appropriate instructions.
    """

    async def process_agent_transfer(self, request: LlmRequest, agent: "LlmAgent",
                                     ctx: InvocationContext) -> Optional[Event]:
        """Add agent transfer capabilities to the LLM request.

        This method:
        1. Identifies valid transfer targets for the agent
        2. Adds instructions about available agents
        3. Ensures the transfer_to_agent tool is available in the agent's tools

        Args:
            request: The model request to add transfer capabilities to
            agent: The LlmAgent to process transfer for
            ctx: The invocation context

        Returns:
            Event: Error event if processing fails, None if successful
        """
        try:
            # Get transfer targets for this agent
            transfer_targets = self._get_transfer_targets(agent)

            if not transfer_targets:
                logger.debug("No transfer targets found for agent: %s", agent.name)
                return None

            # Add transfer instructions to the request
            transfer_instructions = self._build_transfer_instructions(agent, transfer_targets)
            if transfer_instructions:
                # Add to system prompt - append to existing instructions
                current_system = request.config.system_instruction or ""
                if current_system:
                    combined_instructions = f"{current_system}\n\n{transfer_instructions}"
                else:
                    combined_instructions = transfer_instructions
                request.config.system_instruction = combined_instructions

                logger.debug("Added transfer instructions for %s targets to agent: %s", len(transfer_targets),
                             agent.name)

            # The transfer_to_agent tool should already be included in the agent's tools
            # when agent transfer is enabled. The tools processor will handle it automatically.
            logger.debug("Agent transfer processing completed for agent: %s", agent.name)

            return None  # Success

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error processing agent transfer for agent %s: %s", agent.name, ex)
            return self._create_error_event(ctx, "agent_transfer_error", f"Failed to process agent transfer: {str(ex)}")

    def _create_error_event(self, ctx: InvocationContext, error_code: str, error_message: str) -> Event:
        """Create an error event with the agent name from context.

        Args:
            ctx: The invocation context containing agent information
            error_code: The error code for the event
            error_message: The error message for the event

        Returns:
            Event: Error event with proper attribution
        """
        return Event(
            invocation_id=ctx.invocation_id,
            author=ctx.agent.name,
            error_code=error_code,
            error_message=error_message,
        )

    def _get_transfer_targets(self, agent: "LlmAgent") -> List[BaseAgent]:
        """Get valid transfer targets for the given agent.

        Transfer targets include:
        - All sub-agents of the current agent
        - Parent agent (if transfer to parent is allowed)
        - Peer agents (if transfer to peers is allowed)

        Args:
            agent: The agent to get transfer targets for

        Returns:
            List of valid transfer target agents
        """
        targets = []

        # Add sub-agents
        targets.extend(agent.sub_agents)

        # Add parent agent if allowed and exists
        if agent.parent_agent and not agent.disallow_transfer_to_parent and self._is_llm_agent(agent.parent_agent):
            targets.append(agent.parent_agent)

        # Add peer agents if allowed
        if not agent.disallow_transfer_to_peers and agent.parent_agent and self._is_llm_agent(agent.parent_agent):
            # Get all siblings (peer agents)
            peer_agents = [peer_agent for peer_agent in agent.parent_agent.sub_agents if peer_agent.name != agent.name]
            targets.extend(peer_agents)

        logger.debug("Found %s transfer targets for agent %s: %s", len(targets), agent.name, [t.name for t in targets])

        return targets

    def _is_llm_agent(self, agent: BaseAgent) -> bool:
        """Check whether *agent* is LlmAgent-compatible without importing LlmAgent.

        Uses runtime class MRO names so it also works for subclasses of LlmAgent.
        This avoids circular imports in transfer processing code paths.
        """
        cls = agent.get_agent_class()
        return any(base.__name__ == "LlmAgent" for base in cls.__mro__)

    def _build_transfer_instructions(self, agent: "LlmAgent", target_agents: List[BaseAgent]) -> str:
        """Build transfer instructions for the LLM.

        Args:
            agent: The current agent
            target_agents: List of available transfer targets

        Returns:
            Instruction text for the LLM about agent transfers
        """
        if not target_agents:
            return ""

        # Check if agent has custom default_transfer_message
        # If default_transfer_message is not None, use it (even if it's an empty string)
        if hasattr(agent, 'default_transfer_message') and agent.default_transfer_message is not None:
            return agent.default_transfer_message

        # Build information about each target agent
        target_info_lines = []
        for target_agent in target_agents:
            target_info = self._build_target_agent_info(target_agent)
            target_info_lines.append(target_info)

        # Build the main transfer instructions
        instructions = f"""
You have a list of other agents to transfer to:

{chr(10).join(target_info_lines)}

If you are the best to answer the question according to your description, you
can answer it.

If another agent is better for answering the question according to its
description, call `transfer_to_agent` function to transfer the
question to that agent. When transferring, do not generate any text other than
the function call.
"""

        # Add parent agent guidance if applicable
        if agent.parent_agent and not agent.disallow_transfer_to_parent and self._is_llm_agent(agent.parent_agent):
            instructions += f"""
Your parent agent is {agent.parent_agent.name}. If neither the other agents nor
you are best for answering the question according to the descriptions, transfer
to your parent agent.
"""

        return instructions.strip()

    def _build_target_agent_info(self, target_agent: BaseAgent) -> str:
        """Build information string for a target agent.

        Args:
            target_agent: The agent to build info for

        Returns:
            Formatted agent information string
        """
        return f"""
Agent name: {target_agent.name}
Agent description: {target_agent.description}
"""


# Create a default instance for convenience
default_agent_transfer_processor = AgentTransferProcessor()
