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
from .nodes import NODE_ID_TRANSFORM1
from .nodes import map_input_llmagent1
from .nodes import node_end1
from .nodes import node_start
from .nodes import node_transform1
from .prompts import LLMAGENT1_INSTRUCTION
from .state import WorkflowState


def _create_llmagent1_agent() -> LlmAgent:
    generation_config = GenerateContentConfig(temperature=0.0, )
    tools = []
    return LlmAgent(
        name=NODE_ID_LLMAGENT1,
        description='Echo agent',
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
        config=NodeConfig(name=NODE_ID_START, description='Start'),
    )
    graph.add_node(
        NODE_ID_TRANSFORM1,
        node_transform1,
        config=NodeConfig(name=NODE_ID_TRANSFORM1, description='Build per-node user message'),
    )
    graph.add_node(
        NODE_ID_END1,
        node_end1,
        config=NodeConfig(name=NODE_ID_END1, description='End'),
    )
    graph.add_agent_node(
        NODE_ID_LLMAGENT1,
        _create_llmagent1_agent(),
        input_mapper=map_input_llmagent1,
        config=NodeConfig(name=NODE_ID_LLMAGENT1, description='Echo agent'),
    )

    graph.add_edge(NODE_ID_START, NODE_ID_TRANSFORM1)
    graph.add_edge(NODE_ID_TRANSFORM1, NODE_ID_LLMAGENT1)
    graph.add_edge(NODE_ID_LLMAGENT1, NODE_ID_END1)
    graph.set_entry_point(NODE_ID_START)

    return GraphAgent(
        name='user_message_override',
        description=
        'Demonstrates builtin.llmagent.config.user_message overriding the per-node user message with upstream outputs.',
        graph=graph.compile(),
    )


root_agent = create_agent()
