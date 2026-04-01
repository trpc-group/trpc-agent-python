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
from .config import create_model_llmagent3
from .config import create_model_llmagent4
from .nodes import NODE_ID_END1
from .nodes import NODE_ID_END2
from .nodes import NODE_ID_END3
from .nodes import NODE_ID_END4
from .nodes import NODE_ID_LLMAGENT1
from .nodes import NODE_ID_LLMAGENT2
from .nodes import NODE_ID_LLMAGENT3
from .nodes import NODE_ID_LLMAGENT4
from .nodes import NODE_ID_START
from .nodes import NODE_ID_USER_APPROVAL1
from .nodes import node_end1
from .nodes import node_end2
from .nodes import node_end3
from .nodes import node_end4
from .nodes import node_start
from .nodes import node_user_approval1
from .nodes import route_func1
from .nodes import route_func2
from .prompts import LLMAGENT1_INSTRUCTION
from .prompts import LLMAGENT2_INSTRUCTION
from .prompts import LLMAGENT3_INSTRUCTION
from .prompts import LLMAGENT4_INSTRUCTION
from .state import Llmagent1OutputModel
from .state import WorkflowState


def _create_llmagent1_agent() -> LlmAgent:
    generation_config = GenerateContentConfig(temperature=0.7, )
    tools = []
    return LlmAgent(
        name=NODE_ID_LLMAGENT1,
        description='Classification agent',
        model=create_model_llmagent1(),
        instruction=LLMAGENT1_INSTRUCTION,
        tools=tools,
        generate_content_config=generation_config,
        output_schema=Llmagent1OutputModel,
    )


def _create_llmagent2_agent() -> LlmAgent:
    generation_config = GenerateContentConfig(
        temperature=0.7,
        max_output_tokens=512,
    )
    tools = []
    return LlmAgent(
        name=NODE_ID_LLMAGENT2,
        description='Return agent',
        model=create_model_llmagent2(),
        instruction=LLMAGENT2_INSTRUCTION,
        tools=tools,
        generate_content_config=generation_config,
    )


def _create_llmagent3_agent() -> LlmAgent:
    generation_config = GenerateContentConfig(
        temperature=0.7,
        max_output_tokens=512,
    )
    tools = []
    return LlmAgent(
        name=NODE_ID_LLMAGENT3,
        description='Retention agent',
        model=create_model_llmagent3(),
        instruction=LLMAGENT3_INSTRUCTION,
        tools=tools,
        generate_content_config=generation_config,
    )


def _create_llmagent4_agent() -> LlmAgent:
    generation_config = GenerateContentConfig(
        temperature=0.7,
        max_output_tokens=1024,
    )
    tools = []
    return LlmAgent(
        name=NODE_ID_LLMAGENT4,
        description='Information agent',
        model=create_model_llmagent4(),
        instruction=LLMAGENT4_INSTRUCTION,
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
        config=NodeConfig(name=NODE_ID_END1, description='Return end'),
    )
    graph.add_node(
        NODE_ID_END2,
        node_end2,
        config=NodeConfig(name=NODE_ID_END2, description='Retention end'),
    )
    graph.add_node(
        NODE_ID_END3,
        node_end3,
        config=NodeConfig(name=NODE_ID_END3, description='Retention rejected end'),
    )
    graph.add_node(
        NODE_ID_END4,
        node_end4,
        config=NodeConfig(name=NODE_ID_END4, description='Information end'),
    )
    graph.add_node(
        NODE_ID_USER_APPROVAL1,
        node_user_approval1,
        config=NodeConfig(name=NODE_ID_USER_APPROVAL1, description='User approval'),
    )
    graph.add_agent_node(
        NODE_ID_LLMAGENT1,
        _create_llmagent1_agent(),
        config=NodeConfig(name=NODE_ID_LLMAGENT1, description='Classification agent'),
    )
    graph.add_agent_node(
        NODE_ID_LLMAGENT2,
        _create_llmagent2_agent(),
        config=NodeConfig(name=NODE_ID_LLMAGENT2, description='Return agent'),
    )
    graph.add_agent_node(
        NODE_ID_LLMAGENT3,
        _create_llmagent3_agent(),
        config=NodeConfig(name=NODE_ID_LLMAGENT3, description='Retention agent'),
    )
    graph.add_agent_node(
        NODE_ID_LLMAGENT4,
        _create_llmagent4_agent(),
        config=NodeConfig(name=NODE_ID_LLMAGENT4, description='Information agent'),
    )

    graph.add_edge(NODE_ID_START, NODE_ID_LLMAGENT1)
    graph.add_edge(NODE_ID_LLMAGENT2, NODE_ID_END1)
    graph.add_edge(NODE_ID_LLMAGENT3, NODE_ID_USER_APPROVAL1)
    graph.add_edge(NODE_ID_LLMAGENT4, NODE_ID_END4)
    graph.add_edge(NODE_ID_END1, END)
    graph.add_edge(NODE_ID_END2, END)
    graph.add_edge(NODE_ID_END3, END)
    graph.add_edge(NODE_ID_END4, END)
    graph.add_conditional_edges(NODE_ID_LLMAGENT1, route_func1)
    graph.add_conditional_edges(NODE_ID_USER_APPROVAL1, route_func2)
    graph.set_entry_point(NODE_ID_START)

    return GraphAgent(
        name='openai_custom_service',
        description=
        'Replicates OpenAI Agent Builder custom service routing: return_item / cancel_subscription / get_information.',
        graph=graph.compile(),
    )


root_agent = create_agent()
