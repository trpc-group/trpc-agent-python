# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
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

from __future__ import annotations

import re
from typing import Dict
from typing import List
from typing import Optional

from a2a.types import AgentCapabilities
from a2a.types import AgentCard
from a2a.types import AgentExtension
from a2a.types import AgentProvider
from a2a.types import AgentSkill
from a2a.types import SecurityScheme

from trpc_agent_sdk.abc import ToolSetABC as BaseToolSet
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.agents import ChainAgent as SequentialAgent
from trpc_agent_sdk.agents import CycleAgent as LoopAgent
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import ParallelAgent
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.tools import convert_toolunion_to_tool_list

from ._constants import EXTENSION_TRPC_A2A_VERSION
from ._constants import INTERACTION_SPEC_VERSION


class AgentCardBuilder:
    """Builder class for creating agent cards from TrpcAgent agents.

    This class provides functionality to convert TrpcAgent agents into A2A agent cards,
    including extracting skills, capabilities, and metadata from various agent
    types.
    """

    def __init__(
        self,
        *,
        agent: BaseAgent,
        rpc_url: Optional[str] = None,
        capabilities: Optional[AgentCapabilities] = None,
        doc_url: Optional[str] = None,
        provider: Optional[AgentProvider] = None,
        agent_version: Optional[str] = None,
        security_schemes: Optional[Dict[str, SecurityScheme]] = None,
    ):
        if not agent:
            raise ValueError('Agent cannot be None or empty.')

        self._agent = agent
        # keep it empty, trpc-a2a server will replace it with yaml config
        self._rpc_url = rpc_url or ''
        self._capabilities = capabilities or AgentCapabilities()
        self._doc_url = doc_url
        self._provider = provider
        self._security_schemes = security_schemes
        self._agent_version = agent_version or '0.0.1'

    async def build(self) -> AgentCard:
        """Build and return the complete agent card."""
        try:
            primary_skills = await _build_primary_skills(self._agent)
            sub_agent_skills = await _build_sub_agent_skills(self._agent)
            all_skills = primary_skills + sub_agent_skills

            # Add trpc-a2a-version to capabilities.extensions.
            capabilities = _capabilities_with_trpc_extension(self._capabilities)

            return AgentCard(
                name=self._agent.name,
                description=self._agent.description or 'An A2A Agent',
                doc_url=self._doc_url,
                url=f"{self._rpc_url.rstrip('/')}",
                version=self._agent_version,
                capabilities=capabilities,
                skills=all_skills,
                default_input_modes=['text/plain'],
                default_output_modes=['text/plain'],
                supports_authenticated_extended_card=False,
                provider=self._provider,
                security_schemes=self._security_schemes,
            )
        except Exception as ex:  # pylint: disable=broad-except
            raise RuntimeError(f'Failed to build agent card for {self._agent.name}: {ex}') from ex


def _capabilities_with_trpc_extension(capabilities: Optional[AgentCapabilities]) -> AgentCapabilities:
    """Ensure capabilities includes the trpc-a2a-version extension."""
    base = capabilities or AgentCapabilities()
    exts = list(base.extensions) if base.extensions else []
    if not any(getattr(e, "uri", None) == EXTENSION_TRPC_A2A_VERSION for e in exts):
        exts.append(AgentExtension(
            uri=EXTENSION_TRPC_A2A_VERSION,
            params={"version": INTERACTION_SPEC_VERSION},
        ))
    return base.model_copy(update={"extensions": exts})


# Module-level helper functions
async def _build_primary_skills(agent: BaseAgent) -> List[AgentSkill]:
    """Build skills for any agent type."""
    if isinstance(agent, LlmAgent):
        return await _build_llm_agent_skills(agent)
    else:
        return await _build_non_llm_agent_skills(agent)


async def _build_llm_agent_skills(agent: LlmAgent) -> List[AgentSkill]:
    """Build skills for LLM agent."""
    skills = []

    # 1. Agent skill (main model skill)
    agent_description = _build_llm_agent_description_with_instructions(agent)

    skills.append(
        AgentSkill(
            id=agent.name,
            name='model',
            description=agent_description,
            examples=None,
            input_modes=_get_input_modes(agent),
            output_modes=_get_output_modes(agent),
            tags=['llm'],
        ))

    # 2. Tool skills
    if agent.tools:
        try:
            tool_skills = await _build_tool_skills(agent)
            skills.extend(tool_skills)
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Failed to build tool skills: %s", ex)

    # 3. Planner skill
    if agent.planner:
        skills.append(_build_planner_skill(agent))

    # 4. Code executor skill
    # TODO: add code executor skill
    # if agent.code_executor:
    #   skills.append(_build_code_executor_skill(agent))

    return skills


async def _build_sub_agent_skills(agent: BaseAgent) -> List[AgentSkill]:
    """Build skills for all sub-agents."""
    sub_agent_skills = []
    for sub_agent in agent.sub_agents:
        try:
            sub_skills = await _build_primary_skills(sub_agent)
            for skill in sub_skills:
                # Create a new skill instance to avoid modifying original if shared
                aggregated_skill = AgentSkill(
                    id=f'{sub_agent.name}_{skill.id}',
                    name=f'{sub_agent.name}: {skill.name}',
                    description=skill.description,
                    examples=skill.examples,
                    input_modes=skill.input_modes,
                    output_modes=skill.output_modes,
                    tags=[f'sub_agent:{sub_agent.name}'] + (skill.tags or []),
                )
                sub_agent_skills.append(aggregated_skill)
        except Exception as ex:  # pylint: disable=broad-except
            # Log warning but continue with other sub-agents
            logger.warning("Failed to build skills for sub-agent %s: %s", sub_agent.name, ex)
            continue

    return sub_agent_skills


async def _build_tool_skills(agent: LlmAgent) -> List[AgentSkill]:
    """Build skills for agent tools."""
    tool_skills = []
    canonical_tools = await convert_toolunion_to_tool_list(agent.tools, None)

    for tool in canonical_tools:
        tool_name = (tool.name if hasattr(tool, 'name') and tool.name else tool.__class__.__name__)

        tool_skills.append(
            AgentSkill(
                id=f'{agent.name}-{tool_name}',
                name=tool_name,
                description=getattr(tool, 'description', f'Tool: {tool_name}'),
                examples=None,
                input_modes=None,
                output_modes=None,
                tags=['llm', 'tools'],
            ))

    # A2A Only extract tool information instead of using tool at initialize.
    # So we should close the tool's resources as Agent may not running in the same EventLoop as initialize process.
    for toolset in agent.tools:
        if isinstance(toolset, BaseToolSet):
            await toolset.close()

    return tool_skills


def _build_planner_skill(agent: LlmAgent) -> AgentSkill:
    """Build planner skill for LLM agent."""
    return AgentSkill(
        id=f'{agent.name}-planner',
        name='planning',
        description='Can think about the tasks to do and make plans',
        examples=None,
        input_modes=None,
        output_modes=None,
        tags=['llm', 'planning'],
    )


def _build_code_executor_skill(agent: LlmAgent) -> AgentSkill:
    """Build code executor skill for LLM agent."""
    return AgentSkill(
        id=f'{agent.name}-code-executor',
        name='code-execution',
        description='Can execute code',
        examples=None,
        input_modes=None,
        output_modes=None,
        tags=['llm', 'code_execution'],
    )


async def _build_non_llm_agent_skills(agent: BaseAgent) -> List[AgentSkill]:
    """Build skills for non-LLM agents."""
    skills = []

    # 1. Agent skill (main agent skill)
    agent_description = _build_agent_description(agent)

    # Determine agent type and name
    agent_type = _get_agent_type(agent)
    agent_name = _get_agent_skill_name(agent)

    skills.append(
        AgentSkill(
            id=agent.name,
            name=agent_name,
            description=agent_description,
            examples=None,
            input_modes=_get_input_modes(agent),
            output_modes=_get_output_modes(agent),
            tags=[agent_type],
        ))

    # 2. Sub-agent orchestration skill (for agents with sub-agents)
    if agent.sub_agents:
        orchestration_skill = _build_orchestration_skill(agent, agent_type)
        if orchestration_skill:
            skills.append(orchestration_skill)

    return skills


def _build_orchestration_skill(agent: BaseAgent, agent_type: str) -> Optional[AgentSkill]:
    """Build orchestration skill for agents with sub-agents."""
    sub_agent_descriptions = []
    for sub_agent in agent.sub_agents:
        description = sub_agent.description or 'No description'
        sub_agent_descriptions.append(f'{sub_agent.name}: {description}')

    if not sub_agent_descriptions:
        return None

    return AgentSkill(
        id=f'{agent.name}-sub-agents',
        name='sub-agents',
        description='Orchestrates: ' + '; '.join(sub_agent_descriptions),
        examples=None,
        input_modes=None,
        output_modes=None,
        tags=[agent_type, 'orchestration'],
    )


def _get_agent_type(agent: BaseAgent) -> str:
    """Get the agent type for tagging."""
    if isinstance(agent, LlmAgent):
        return 'llm'
    elif isinstance(agent, SequentialAgent):
        return 'sequential_workflow'
    elif isinstance(agent, ParallelAgent):
        return 'parallel_workflow'
    elif isinstance(agent, LoopAgent):
        return 'loop_workflow'
    else:
        return 'custom_agent'


def _get_agent_skill_name(agent: BaseAgent) -> str:
    """Get the skill name based on agent type."""
    if isinstance(agent, LlmAgent):
        return 'model'
    elif isinstance(agent, (SequentialAgent, ParallelAgent, LoopAgent)):
        return 'workflow'
    else:
        return 'custom'


def _build_agent_description(agent: BaseAgent) -> str:
    """Build agent description from agent.description and workflow-specific descriptions."""
    description_parts = []

    # Add agent description
    if agent.description:
        description_parts.append(agent.description)

    # Add workflow-specific descriptions for non-LLM agents
    if not isinstance(agent, LlmAgent):
        workflow_description = _get_workflow_description(agent)
        if workflow_description:
            description_parts.append(workflow_description)

    return (' '.join(description_parts) if description_parts else _get_default_description(agent))


def _build_llm_agent_description_with_instructions(agent: LlmAgent) -> str:
    """Build agent description including instructions for LlmAgents."""
    description_parts = []

    # Add agent description
    if agent.description:
        description_parts.append(agent.description)

    # Add instruction (with pronoun replacement) - only for LlmAgent
    if agent.instruction:
        instruction = _replace_pronouns(agent.instruction)
        description_parts.append(instruction)

    # Add global instruction (with pronoun replacement) - only for LlmAgent
    if agent.global_instruction:
        global_instruction = _replace_pronouns(agent.global_instruction)
        description_parts.append(global_instruction)

    return (' '.join(description_parts) if description_parts else _get_default_description(agent))


def _replace_pronouns(text: str) -> str:
    """Replace pronouns and conjugate common verbs for agent description.
        (e.g., "You are" -> "I am", "your" -> "my").
        """
    pronoun_map = {
        # Longer phrases with verb conjugations
        'you are': 'I am',
        'you were': 'I was',
        "you're": 'I am',
        "you've": 'I have',
        # Standalone pronouns
        'yours': 'mine',
        'your': 'my',
        'you': 'I',
    }

    # Sort keys by length (descending) to ensure longer phrases are matched first.
    # This prevents "you" in "you are" from being replaced on its own.
    sorted_keys = sorted(pronoun_map.keys(), key=len, reverse=True)

    pattern = r'\b(' + '|'.join(re.escape(key) for key in sorted_keys) + r')\b'

    return re.sub(
        pattern,
        lambda match: pronoun_map[match.group(1).lower()],
        text,
        flags=re.IGNORECASE,
    )


def _get_workflow_description(agent: BaseAgent) -> Optional[str]:
    """Get workflow-specific description for non-LLM agents."""
    if not agent.sub_agents:
        return None

    if isinstance(agent, SequentialAgent):
        return _build_sequential_description(agent)
    elif isinstance(agent, ParallelAgent):
        return _build_parallel_description(agent)
    elif isinstance(agent, LoopAgent):
        return _build_loop_description(agent)

    return None


def _build_sequential_description(agent: SequentialAgent) -> str:
    """Build description for sequential workflow agent."""
    descriptions = []
    for i, sub_agent in enumerate(agent.sub_agents, 1):
        sub_description = (sub_agent.description or f'execute the {sub_agent.name} agent')
        if i == 1:
            descriptions.append(f'First, this agent will {sub_description}')
        elif i == len(agent.sub_agents):
            descriptions.append(f'Finally, this agent will {sub_description}')
        else:
            descriptions.append(f'Then, this agent will {sub_description}')
    return ' '.join(descriptions) + '.'


def _build_parallel_description(agent: ParallelAgent) -> str:
    """Build description for parallel workflow agent."""
    descriptions = []
    for i, sub_agent in enumerate(agent.sub_agents):
        sub_description = (sub_agent.description or f'execute the {sub_agent.name} agent')
        if i == 0:
            descriptions.append(f'This agent will {sub_description}')
        elif i == len(agent.sub_agents) - 1:
            descriptions.append(f'and {sub_description}')
        else:
            descriptions.append(f', {sub_description}')
    return ' '.join(descriptions) + ' simultaneously.'


def _build_loop_description(agent: LoopAgent) -> str:
    """Build description for loop workflow agent."""
    max_iterations = agent.max_iterations or 'unlimited'
    descriptions = []
    for i, sub_agent in enumerate(agent.sub_agents):
        sub_description = (sub_agent.description or f'execute the {sub_agent.name} agent')
        if i == 0:
            descriptions.append(f'This agent will {sub_description}')
        elif i == len(agent.sub_agents) - 1:
            descriptions.append(f'and {sub_description}')
        else:
            descriptions.append(f', {sub_description}')
    return (f"{' '.join(descriptions)} in a loop (max {max_iterations} iterations).")


def _get_default_description(agent: BaseAgent) -> str:
    """Get default description based on agent type."""
    agent_type_descriptions = {
        LlmAgent: 'An LLM-based agent',
        SequentialAgent: 'A sequential workflow agent',
        ParallelAgent: 'A parallel workflow agent',
        LoopAgent: 'A loop workflow agent',
    }

    for agent_type, description in agent_type_descriptions.items():
        if isinstance(agent, agent_type):
            return description

    return 'A custom agent'


def _get_input_modes(agent: BaseAgent) -> Optional[List[str]]:
    """Get input modes based on agent model."""
    if not isinstance(agent, LlmAgent):
        return None

    # This could be enhanced to check model capabilities
    # For now, return None to use default_input_modes
    return None


def _get_output_modes(agent: BaseAgent) -> Optional[List[str]]:
    """Get output modes from Agent.generate_content_config.response_modalities."""
    if not isinstance(agent, LlmAgent):
        return None

    if (hasattr(agent, 'generate_content_config') and agent.generate_content_config
            and hasattr(agent.generate_content_config, 'response_modalities')):
        return agent.generate_content_config.response_modalities

    return None
