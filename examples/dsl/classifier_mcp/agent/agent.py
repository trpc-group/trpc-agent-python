# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Generated graph workflow construction."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.dsl.graph import END
from trpc_agent_sdk.dsl.graph import GraphAgent
from trpc_agent_sdk.dsl.graph import NodeConfig
from trpc_agent_sdk.dsl.graph import StateGraph
from trpc_agent_sdk.types import GenerateContentConfig

from .config import create_model_llmagent1
from .config import create_model_llmagent2
from .config import create_model_llmagent3
from .nodes import NODE_ID_END1
from .nodes import NODE_ID_END2
from .nodes import NODE_ID_LLMAGENT1
from .nodes import NODE_ID_LLMAGENT2
from .nodes import NODE_ID_LLMAGENT3
from .nodes import NODE_ID_START
from .nodes import node_end1
from .nodes import node_end2
from .nodes import node_start
from .nodes import route_func1
from .prompts import LLMAGENT1_INSTRUCTION
from .prompts import LLMAGENT2_INSTRUCTION
from .prompts import LLMAGENT3_INSTRUCTION
from .state import Llmagent1OutputModel
from .state import WorkflowState
from .tools import create_tools_llmagent2
from .tools import create_tools_llmagent3


def _create_llmagent1_agent() -> LlmAgent:
    generation_config = GenerateContentConfig(temperature=0.3, )
    tools = []
    return LlmAgent(
        name=NODE_ID_LLMAGENT1,
        description='Classifier Agent',
        model=create_model_llmagent1(),
        instruction=LLMAGENT1_INSTRUCTION,
        tools=tools,
        generate_content_config=generation_config,
        output_schema=Llmagent1OutputModel,
    )


def _create_llmagent2_agent() -> LlmAgent:
    generation_config = GenerateContentConfig(
        temperature=0.5,
        max_output_tokens=1024,
    )
    tools = create_tools_llmagent2()
    return LlmAgent(
        name=NODE_ID_LLMAGENT2,
        description='Simple Math Agent',
        model=create_model_llmagent2(),
        instruction=LLMAGENT2_INSTRUCTION,
        tools=tools,
        generate_content_config=generation_config,
    )


def _create_llmagent3_agent() -> LlmAgent:
    generation_config = GenerateContentConfig(
        temperature=0.5,
        max_output_tokens=1024,
    )
    tools = create_tools_llmagent3()
    return LlmAgent(
        name=NODE_ID_LLMAGENT3,
        description='Complex Math Agent',
        model=create_model_llmagent3(),
        instruction=LLMAGENT3_INSTRUCTION,
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
        NODE_ID_END1,
        node_end1,
        config=NodeConfig(name=NODE_ID_END1, description='Simple Math End'),
    )
    graph.add_node(
        NODE_ID_END2,
        node_end2,
        config=NodeConfig(name=NODE_ID_END2, description='Complex Math End'),
    )
    graph.add_agent_node(
        NODE_ID_LLMAGENT1,
        _create_llmagent1_agent(),
        config=NodeConfig(name=NODE_ID_LLMAGENT1, description='Classifier Agent'),
    )
    graph.add_agent_node(
        NODE_ID_LLMAGENT2,
        _create_llmagent2_agent(),
        config=NodeConfig(name=NODE_ID_LLMAGENT2, description='Simple Math Agent'),
    )
    graph.add_agent_node(
        NODE_ID_LLMAGENT3,
        _create_llmagent3_agent(),
        config=NodeConfig(name=NODE_ID_LLMAGENT3, description='Complex Math Agent'),
    )

    graph.add_edge(NODE_ID_START, NODE_ID_LLMAGENT1)
    graph.add_edge(NODE_ID_LLMAGENT2, NODE_ID_END1)
    graph.add_edge(NODE_ID_LLMAGENT3, NODE_ID_END2)
    graph.add_edge(NODE_ID_END1, END)
    graph.add_edge(NODE_ID_END2, END)
    graph.add_conditional_edges(NODE_ID_LLMAGENT1, route_func1)
    graph.set_entry_point(NODE_ID_START)

    return GraphAgent(
        name='classifier_mcp_example',
        description='Classifier agent routes to two worker agents with MCP calculator tools (SSE transport)',
        graph=graph.compile(),
    )


root_agent = create_agent()
