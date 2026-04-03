# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Graph construction for graph_multi_turns example."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.dsl.graph import GraphAgent
from trpc_agent_sdk.dsl.graph import NodeConfig
from trpc_agent_sdk.dsl.graph import STATE_KEY_USER_INPUT
from trpc_agent_sdk.dsl.graph import StateGraph
from trpc_agent_sdk.dsl.graph import StateMapper
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.types import GenerateContentConfig

from .callbacks import create_node_callbacks
from .config import get_model_config
from .nodes import ROUTE_AGENT
from .nodes import ROUTE_LLM
from .nodes import decide_route
from .nodes import format_output
from .nodes import route_choice
from .prompts import AGENT_NODE_WORKER_INSTRUCTION
from .prompts import LLM_NODE_INSTRUCTION
from .state import MultiTurnState


def _create_model() -> OpenAIModel:
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)


def _create_branch_agent_worker() -> LlmAgent:
    return LlmAgent(
        name="branch_agent_worker",
        description="Sub-agent used by agent_node branch in multi-turn graph demo",
        model=_create_model(),
        instruction=AGENT_NODE_WORKER_INSTRUCTION,
        generate_content_config=GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=220,
        ),
        disallow_transfer_to_parent=True,
    )


def _create_graph(worker_agent: LlmAgent) -> StateGraph:
    callbacks = create_node_callbacks()
    graph = StateGraph(MultiTurnState, callbacks=callbacks)

    graph.add_node(
        "decide",
        decide_route,
        config=NodeConfig(name="decide", description="Selects llm or agent branch"),
    )

    graph.add_llm_node(
        "llm_reply_node",
        _create_model(),
        LLM_NODE_INSTRUCTION,
        tools={},
        generation_config=GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=260,
        ),
        config=NodeConfig(name="llm_reply_node", description="Replies using llm_node"),
    )

    graph.add_agent_node(
        "agent_reply_node",
        worker_agent,
        config=NodeConfig(name="agent_reply_node", description="Replies using agent_node"),
        input_mapper=StateMapper.rename({"query_text": STATE_KEY_USER_INPUT}),
        output_mapper=StateMapper.merge_response("agent_reply"),
    )

    graph.add_node(
        "format_output",
        format_output,
        config=NodeConfig(name="format_output", description="Formats the turn response"),
    )

    graph.set_entry_point("decide")
    graph.set_finish_point("format_output")

    graph.add_conditional_edges(
        "decide",
        route_choice,
        {
            ROUTE_LLM: "llm_reply_node",
            ROUTE_AGENT: "agent_reply_node",
        },
    )
    graph.add_edge("llm_reply_node", "format_output")
    graph.add_edge("agent_reply_node", "format_output")

    return graph


def create_agent() -> GraphAgent:
    worker_agent = _create_branch_agent_worker()
    compiled = _create_graph(worker_agent).compile()
    return GraphAgent(
        name="graph_multi_turns",
        description="Graph demo for multi-turn conversation across llm_node and agent_node branches",
        graph=compiled,
    )


root_agent = create_agent()
