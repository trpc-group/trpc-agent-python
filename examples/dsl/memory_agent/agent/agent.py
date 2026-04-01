# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Generated graph workflow construction."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.dsl.graph import GraphAgent
from trpc_agent_sdk.dsl.graph import NodeConfig
from trpc_agent_sdk.dsl.graph import StateGraph

from .config import create_model_llmagent1
from .nodes import NODE_ID_END1
from .nodes import NODE_ID_LLMAGENT1
from .nodes import NODE_ID_START
from .nodes import node_end1
from .nodes import node_start
from .prompts import LLMAGENT1_INSTRUCTION
from .state import WorkflowState
from .tools import create_tools_llmagent1


def _create_llmagent1_agent() -> LlmAgent:
    generation_config = None
    tools = create_tools_llmagent1()
    return LlmAgent(
        name=NODE_ID_LLMAGENT1,
        description='Memory Agent',
        model=create_model_llmagent1(),
        instruction=LLMAGENT1_INSTRUCTION,
        tools=tools,
        generate_content_config=generation_config,
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
        config=NodeConfig(name=NODE_ID_LLMAGENT1, description='Memory Agent'),
    )

    graph.add_edge(NODE_ID_START, NODE_ID_LLMAGENT1)
    graph.add_edge(NODE_ID_LLMAGENT1, NODE_ID_END1)
    graph.set_entry_point(NODE_ID_START)

    return GraphAgent(
        name='memory-agent-auto',
        description='Demo builtin.llmagent with memory_search tool (auto memory extraction is runner-level).',
        graph=graph.compile(),
    )


root_agent = create_agent()
