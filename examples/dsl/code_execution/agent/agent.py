# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Generated graph workflow construction."""

from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
from trpc_agent_sdk.dsl.graph import GraphAgent
from trpc_agent_sdk.dsl.graph import NodeConfig
from trpc_agent_sdk.dsl.graph import StateGraph

from .nodes import NODE_ID_CODE1
from .nodes import NODE_ID_CODE2
from .nodes import NODE_ID_CUSTOM_FORMAT_RESULTS1
from .nodes import NODE_ID_START
from .nodes import node_custom_format_results1
from .nodes import node_start
from .state import WorkflowState
from .tools import CODE_CODE1
from .tools import CODE_CODE2


def create_agent() -> GraphAgent:
    graph = StateGraph(WorkflowState)

    graph.add_node(
        NODE_ID_START,
        node_start,
        config=NodeConfig(name=NODE_ID_START, description='Start'),
    )
    graph.add_code_node(
        NODE_ID_CODE1,
        UnsafeLocalCodeExecutor(
            timeout=30,
            work_dir='',
            clean_temp_files=True,
        ),
        CODE_CODE1,
        'python',
        config=NodeConfig(name=NODE_ID_CODE1, description='Python Data Analysis'),
    )
    graph.add_code_node(
        NODE_ID_CODE2,
        UnsafeLocalCodeExecutor(
            timeout=10,
            work_dir='',
            clean_temp_files=True,
        ),
        CODE_CODE2,
        'bash',
        config=NodeConfig(name=NODE_ID_CODE2, description='Bash System Info'),
    )
    graph.add_node(
        NODE_ID_CUSTOM_FORMAT_RESULTS1,
        node_custom_format_results1,
        config=NodeConfig(name=NODE_ID_CUSTOM_FORMAT_RESULTS1, description='Format Results'),
    )

    graph.add_edge(NODE_ID_START, NODE_ID_CODE1)
    graph.add_edge(NODE_ID_CODE1, NODE_ID_CODE2)
    graph.add_edge(NODE_ID_CODE2, NODE_ID_CUSTOM_FORMAT_RESULTS1)
    graph.set_entry_point(NODE_ID_START)

    return GraphAgent(
        name='Code Execution Workflow',
        description='Demonstrates using builtin.code component to execute Python, JavaScript, and Bash code',
        graph=graph.compile(),
    )


root_agent = create_agent()
