# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Graph construction for graph_with_interrupt example."""

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
from .nodes import ROUTE_APPROVED
from .nodes import ROUTE_REJECTED
from .nodes import approval_gate
from .nodes import finalize_output
from .nodes import route_after_approval
from .prompts import APPROVAL_SUMMARY_AGENT_INSTRUCTION
from .prompts import DRAFT_ACTION_INSTRUCTION
from .state import InterruptState


def _create_model() -> OpenAIModel:
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)


def _create_approval_summary_agent() -> LlmAgent:
    return LlmAgent(
        name="approval_summary_agent",
        description="Summarizes the approved plan after interrupt resume",
        model=_create_model(),
        instruction=APPROVAL_SUMMARY_AGENT_INSTRUCTION,
        generate_content_config=GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=160,
        ),
        disallow_transfer_to_parent=True,
    )


def _create_graph(summary_agent: LlmAgent) -> StateGraph:
    callbacks = create_node_callbacks()
    graph = StateGraph(InterruptState, callbacks=callbacks)

    graph.add_llm_node(
        "draft_action",
        _create_model(),
        DRAFT_ACTION_INSTRUCTION,
        tools={},
        generation_config=GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=220,
        ),
        config=NodeConfig(name="draft_action", description="Draft one recommended action"),
    )

    graph.add_node(
        "approval_gate",
        approval_gate,
        config=NodeConfig(name="approval_gate", description="Interrupt and wait for user decision"),
    )

    graph.add_node(
        "finalize_output",
        finalize_output,
        config=NodeConfig(name="finalize_output", description="Produce final response from decision"),
    )

    graph.add_agent_node(
        "approval_summary_agent_node",
        summary_agent,
        config=NodeConfig(name="approval_summary_agent_node", description="Summarize approved action"),
        input_mapper=StateMapper.rename({"summary_request": STATE_KEY_USER_INPUT}),
        output_mapper=StateMapper.merge_response("approval_summary"),
    )

    graph.set_entry_point("draft_action")
    graph.set_finish_point("finalize_output")

    graph.add_edge("draft_action", "approval_gate")
    graph.add_conditional_edges(
        "approval_gate",
        route_after_approval,
        {
            ROUTE_APPROVED: "approval_summary_agent_node",
            ROUTE_REJECTED: "finalize_output",
        },
    )
    graph.add_edge("approval_summary_agent_node", "finalize_output")

    return graph


def create_agent() -> GraphAgent:
    summary_agent = _create_approval_summary_agent()
    compiled = _create_graph(summary_agent).compile()
    return GraphAgent(
        name="graph_with_interrupt",
        description="Graph demo showing interrupt and resume flow",
        graph=compiled,
    )


root_agent = create_agent()
