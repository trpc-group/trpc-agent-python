# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Generated graph workflow construction."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.dsl.graph import END
from trpc_agent_sdk.dsl.graph import GraphAgent
from trpc_agent_sdk.dsl.graph import NodeConfig
from trpc_agent_sdk.dsl.graph import StateGraph
from trpc_agent_sdk.types import GenerateContentConfig

from .config import create_model_llmagent1
from .config import create_model_llmagent2
from .nodes import NODE_ID_END1
from .nodes import NODE_ID_LLMAGENT1
from .nodes import NODE_ID_LLMAGENT2
from .nodes import NODE_ID_SET_STATE1
from .nodes import NODE_ID_START
from .nodes import NODE_ID_TRANSFORM1
from .nodes import map_input_llmagent1
from .nodes import map_input_llmagent2
from .nodes import node_end1
from .nodes import node_set_state1
from .nodes import node_start
from .nodes import node_transform1
from .prompts import LLMAGENT1_INSTRUCTION
from .prompts import LLMAGENT2_INSTRUCTION
from .state import Llmagent1OutputModel
from .state import WorkflowState


def _create_llmagent1_agent() -> LlmAgent:
    generation_config = GenerateContentConfig(temperature=0.3, )
    tools = []
    return LlmAgent(
        name=NODE_ID_LLMAGENT1,
        description='Query Rewriter',
        model=create_model_llmagent1(),
        instruction=LLMAGENT1_INSTRUCTION,
        tools=tools,
        generate_content_config=generation_config,
        output_schema=Llmagent1OutputModel,
    )


def _create_llmagent2_agent() -> LlmAgent:
    generation_config = GenerateContentConfig(
        temperature=0.7,
        max_output_tokens=2048,
    )
    tools = []
    return LlmAgent(
        name=NODE_ID_LLMAGENT2,
        description='Answer Generator',
        model=create_model_llmagent2(),
        instruction=LLMAGENT2_INSTRUCTION,
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
        config=NodeConfig(name=NODE_ID_TRANSFORM1, description='Format Documents'),
    )
    graph.add_node(
        NODE_ID_END1,
        node_end1,
        config=NodeConfig(name=NODE_ID_END1, description='End'),
    )
    graph.add_node(
        NODE_ID_SET_STATE1,
        node_set_state1,
        config=NodeConfig(name=NODE_ID_SET_STATE1, description='Save Question'),
    )
    graph.add_agent_node(
        NODE_ID_LLMAGENT1,
        _create_llmagent1_agent(),
        input_mapper=map_input_llmagent1,
        config=NodeConfig(name=NODE_ID_LLMAGENT1, description='Query Rewriter'),
    )
    graph.add_agent_node(
        NODE_ID_LLMAGENT2,
        _create_llmagent2_agent(),
        input_mapper=map_input_llmagent2,
        config=NodeConfig(name=NODE_ID_LLMAGENT2, description='Answer Generator'),
    )

    graph.add_edge(NODE_ID_START, NODE_ID_SET_STATE1)
    graph.add_edge(NODE_ID_SET_STATE1, NODE_ID_LLMAGENT1)
    graph.add_edge(NODE_ID_LLMAGENT1, NODE_ID_TRANSFORM1)
    graph.add_edge(NODE_ID_TRANSFORM1, NODE_ID_LLMAGENT2)
    graph.add_edge(NODE_ID_LLMAGENT2, NODE_ID_END1)
    graph.add_edge(NODE_ID_END1, END)
    graph.set_entry_point(NODE_ID_START)

    return GraphAgent(
        name='knowledge_search_example',
        description='Demonstrates Knowledge Search node for RAG (Retrieval-Augmented Generation)',
        graph=graph.compile(),
    )


root_agent = create_agent()
