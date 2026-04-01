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

from .config import create_mcp_toolset_mcp1
from .config import create_model_llmagent1
from .nodes import NODE_ID_END1
from .nodes import NODE_ID_LLMAGENT1
from .nodes import NODE_ID_MCP1
from .nodes import NODE_ID_START
from .nodes import NODE_ID_TRANSFORM1
from .nodes import NODE_ID_TRANSFORM2
from .nodes import node_end1
from .nodes import node_start
from .nodes import node_transform1
from .nodes import node_transform2
from .prompts import LLMAGENT1_INSTRUCTION
from .state import Llmagent1OutputModel
from .state import WorkflowState


def _create_llmagent1_agent() -> LlmAgent:
    generation_config = GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=128,
    )
    tools = []
    return LlmAgent(
        name=NODE_ID_LLMAGENT1,
        description='Planner Agent',
        model=create_model_llmagent1(),
        instruction=LLMAGENT1_INSTRUCTION,
        tools=tools,
        generate_content_config=generation_config,
        output_schema=Llmagent1OutputModel,
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
        config=NodeConfig(name=NODE_ID_TRANSFORM1, description='Prepare MCP request'),
    )
    graph.add_node(
        NODE_ID_TRANSFORM2,
        node_transform2,
        config=NodeConfig(name=NODE_ID_TRANSFORM2, description='Format add summary'),
    )
    graph.add_mcp_node(
        NODE_ID_MCP1,
        create_mcp_toolset_mcp1(),
        selected_tool_name='add',
        req_src_node='prepare_request',
        config=NodeConfig(name=NODE_ID_MCP1, description='Call MCP add'),
    )
    graph.add_node(
        NODE_ID_END1,
        node_end1,
        config=NodeConfig(name=NODE_ID_END1, description='End'),
    )
    graph.add_agent_node(
        NODE_ID_LLMAGENT1,
        _create_llmagent1_agent(),
        config=NodeConfig(name=NODE_ID_LLMAGENT1, description='Planner Agent'),
    )

    graph.add_edge(NODE_ID_START, NODE_ID_LLMAGENT1)
    graph.add_edge(NODE_ID_LLMAGENT1, NODE_ID_TRANSFORM1)
    graph.add_edge(NODE_ID_TRANSFORM1, NODE_ID_MCP1)
    graph.add_edge(NODE_ID_MCP1, NODE_ID_TRANSFORM2)
    graph.add_edge(NODE_ID_TRANSFORM2, NODE_ID_END1)
    graph.set_entry_point(NODE_ID_START)

    return GraphAgent(
        name='mcp_node_weather',
        description='Demonstrates Start -> Agent -> Transform -> MCP(add) -> Transform -> End chain.',
        graph=graph.compile(),
    )


root_agent = create_agent()
