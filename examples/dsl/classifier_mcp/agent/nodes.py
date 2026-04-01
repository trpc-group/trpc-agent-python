# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Node helpers and routing functions for generated graph workflow."""

from typing import Any

from trpc_agent_sdk.dsl.graph import STATE_KEY_NODE_RESPONSES

from .state import WorkflowState

NODE_ID_START = 'start'
NODE_ID_LLMAGENT1 = 'classifier'
NODE_ID_LLMAGENT2 = 'simple_math_agent'
NODE_ID_LLMAGENT3 = 'complex_math_agent'
NODE_ID_END1 = 'simple_end'
NODE_ID_END2 = 'complex_end'


async def node_start(state: WorkflowState) -> dict[str, Any]:
    return {}


async def node_end1(state: WorkflowState) -> dict[str, Any]:
    return {}


async def node_end2(state: WorkflowState) -> dict[str, Any]:
    return {}


def route_func1(state: WorkflowState) -> str:
    if state[STATE_KEY_NODE_RESPONSES][NODE_ID_LLMAGENT1]['classification'] == "math_simple":
        return NODE_ID_LLMAGENT2
    if state[STATE_KEY_NODE_RESPONSES][NODE_ID_LLMAGENT1]['classification'] == "math_complex":
        return NODE_ID_LLMAGENT3
    raise ValueError("No conditional case matched for route from " + NODE_ID_LLMAGENT1)
