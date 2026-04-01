# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Generated graph workflow construction."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.dsl.graph import GraphAgent
from trpc_agent_sdk.dsl.graph import NodeConfig
from trpc_agent_sdk.dsl.graph import StateGraph
from trpc_agent_sdk.types import GenerateContentConfig

from .config import create_model_llmagent1
from .nodes import NODE_ID_END1
from .nodes import NODE_ID_LLMAGENT1
from .nodes import NODE_ID_START
from .nodes import node_end1
from .nodes import node_start
from .prompts import LLMAGENT1_INSTRUCTION
from .state import WorkflowState


def _create_llmagent1_agent() -> LlmAgent:
    generation_config = GenerateContentConfig(temperature=0.7, )
    return LlmAgent(
        name=NODE_ID_LLMAGENT1,
        description='Knowledge Agent',
        model=create_model_llmagent1(),
        instruction=LLMAGENT1_INSTRUCTION,
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
        config=NodeConfig(name=NODE_ID_LLMAGENT1, description='Knowledge Agent'),
    )

    graph.add_edge(NODE_ID_START, NODE_ID_LLMAGENT1)
    graph.add_edge(NODE_ID_LLMAGENT1, NODE_ID_END1)
    graph.set_entry_point(NODE_ID_START)

    return GraphAgent(
        name='knowledge-agent',
        description='Demo agent with knowledge_search tool (agentic mode)',
        graph=graph.compile(),
    )


root_agent = create_agent()
