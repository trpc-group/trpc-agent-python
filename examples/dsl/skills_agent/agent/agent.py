# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Generated graph workflow construction."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.dsl.graph import GraphAgent
from trpc_agent_sdk.dsl.graph import NodeConfig
from trpc_agent_sdk.dsl.graph import StateGraph

from .config import create_model_llmagent1
from .config import create_skill_repository_and_tools_llmagent1
from .nodes import NODE_ID_END1
from .nodes import NODE_ID_LLMAGENT1
from .nodes import NODE_ID_START
from .nodes import node_end1
from .nodes import node_start
from .prompts import LLMAGENT1_INSTRUCTION
from .state import WorkflowState


def _create_llmagent1_agent() -> LlmAgent:
    generation_config = None
    skill_repository, skill_tools = create_skill_repository_and_tools_llmagent1()
    tools = skill_tools + []
    return LlmAgent(
        name=NODE_ID_LLMAGENT1,
        description='Skills Agent',
        model=create_model_llmagent1(),
        instruction=LLMAGENT1_INSTRUCTION,
        tools=tools,
        generate_content_config=generation_config,
        skill_repository=skill_repository,
    )


def create_agent() -> GraphAgent:
    graph = StateGraph(WorkflowState)

    graph.add_node(
        NODE_ID_START,
        node_start,
        config=NodeConfig(name=NODE_ID_START, description='start'),
    )
    graph.add_node(
        NODE_ID_END1,
        node_end1,
        config=NodeConfig(name=NODE_ID_END1, description='end'),
    )
    graph.add_agent_node(
        NODE_ID_LLMAGENT1,
        _create_llmagent1_agent(),
        config=NodeConfig(name=NODE_ID_LLMAGENT1, description='Skills Agent'),
    )

    graph.add_edge(NODE_ID_START, NODE_ID_LLMAGENT1)
    graph.add_edge(NODE_ID_LLMAGENT1, NODE_ID_END1)
    graph.set_entry_point(NODE_ID_START)

    return GraphAgent(
        name='skills-agent-local',
        description='Demo builtin.llmagent with Agent Skills enabled (local executor)',
        graph=graph.compile(),
    )


root_agent = create_agent()
