# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Generated graph workflow construction."""

from trpc_agent_sdk.dsl.graph import GraphAgent
from trpc_agent_sdk.dsl.graph import NodeConfig
from trpc_agent_sdk.dsl.graph import StateGraph

from .nodes import NODE_ID_END1
from .nodes import NODE_ID_START
from .nodes import NODE_ID_TRANSFORM1
from .nodes import NODE_ID_TRANSFORM2
from .nodes import NODE_ID_TRANSFORM3
from .nodes import NODE_ID_TRANSFORM4
from .nodes import node_end1
from .nodes import node_start
from .nodes import node_transform1
from .nodes import node_transform2
from .nodes import node_transform3
from .nodes import node_transform4
from .nodes import route_func1
from .state import WorkflowState


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
        config=NodeConfig(name=NODE_ID_TRANSFORM1, description='Classifier (simulates LLM text output)'),
    )
    graph.add_node(
        NODE_ID_TRANSFORM2,
        node_transform2,
        config=NodeConfig(name=NODE_ID_TRANSFORM2, description='Handle Positive'),
    )
    graph.add_node(
        NODE_ID_TRANSFORM3,
        node_transform3,
        config=NodeConfig(name=NODE_ID_TRANSFORM3, description='Handle Negative'),
    )
    graph.add_node(
        NODE_ID_TRANSFORM4,
        node_transform4,
        config=NodeConfig(name=NODE_ID_TRANSFORM4, description='Handle Neutral'),
    )
    graph.add_node(
        NODE_ID_END1,
        node_end1,
        config=NodeConfig(name=NODE_ID_END1, description='End'),
    )

    graph.add_edge(NODE_ID_START, NODE_ID_TRANSFORM1)
    graph.add_edge(NODE_ID_TRANSFORM2, NODE_ID_END1)
    graph.add_edge(NODE_ID_TRANSFORM3, NODE_ID_END1)
    graph.add_edge(NODE_ID_TRANSFORM4, NODE_ID_END1)
    graph.add_conditional_edges(NODE_ID_TRANSFORM1, route_func1)
    graph.set_entry_point(NODE_ID_START)

    return GraphAgent(
        name='output_text_test',
        description=
        'Tests output_text access for nodes without structured output schema, and conditional edge routing based on output_text.',
        graph=graph.compile(),
    )


root_agent = create_agent()
