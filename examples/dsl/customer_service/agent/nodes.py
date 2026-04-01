# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Node helpers and routing functions for generated graph workflow."""

from typing import Any

from trpc_agent_sdk.dsl.graph import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph import STATE_KEY_NODE_RESPONSES
from trpc_agent_sdk.dsl.graph import interrupt

from .state import WorkflowState

NODE_ID_START = 'start'
NODE_ID_LLMAGENT1 = 'classifier'
NODE_ID_LLMAGENT2 = 'return_agent'
NODE_ID_LLMAGENT3 = 'retention_agent'
NODE_ID_USER_APPROVAL1 = 'retention_approval'
NODE_ID_LLMAGENT4 = 'information_agent'
NODE_ID_END1 = 'return_end'
NODE_ID_END2 = 'retention_end'
NODE_ID_END3 = 'retention_reject_end'
NODE_ID_END4 = 'info_end'


async def node_start(state: WorkflowState) -> dict[str, Any]:
    return {}


async def node_end1(state: WorkflowState) -> dict[str, Any]:
    return {}


async def node_end2(state: WorkflowState) -> dict[str, Any]:
    value = {'message': 'Your retention offer has been accepted. Thank you for staying with us.'}
    last_response = value

    return {
        STATE_KEY_NODE_RESPONSES: {
            NODE_ID_END2: value
        },
        STATE_KEY_LAST_RESPONSE: last_response,
    }


async def node_end3(state: WorkflowState) -> dict[str, Any]:
    value = {'message': 'We understand your decision. If you change your mind, we are always here to help.'}
    last_response = value

    return {
        STATE_KEY_NODE_RESPONSES: {
            NODE_ID_END3: value
        },
        STATE_KEY_LAST_RESPONSE: last_response,
    }


async def node_end4(state: WorkflowState) -> dict[str, Any]:
    return {}


async def node_user_approval1(state: WorkflowState) -> dict[str, Any]:
    desicion = interrupt('Does this retention offer work for you?')["desicion"]
    if desicion not in {"approve", "reject"}:
        raise ValueError("User approval input must be exactly 'approve' or 'reject'")

    return {STATE_KEY_NODE_RESPONSES: {NODE_ID_USER_APPROVAL1: desicion}}


def route_func1(state: WorkflowState) -> str:
    if state[STATE_KEY_NODE_RESPONSES][NODE_ID_LLMAGENT1]['classification'] == "return_item":
        return NODE_ID_LLMAGENT2
    if state[STATE_KEY_NODE_RESPONSES][NODE_ID_LLMAGENT1]['classification'] == "cancel_subscription":
        return NODE_ID_LLMAGENT3
    if state[STATE_KEY_NODE_RESPONSES][NODE_ID_LLMAGENT1]['classification'] == "get_information":
        return NODE_ID_LLMAGENT4
    raise ValueError("No conditional case matched for route from " + NODE_ID_LLMAGENT1)


def route_func2(state: WorkflowState) -> str:
    if state[STATE_KEY_NODE_RESPONSES][NODE_ID_USER_APPROVAL1] == "approve":
        return NODE_ID_END2
    if state[STATE_KEY_NODE_RESPONSES][NODE_ID_USER_APPROVAL1] == "reject":
        return NODE_ID_END3
    raise ValueError(f"Approval decision for {NODE_ID_USER_APPROVAL1} must be 'approve' or 'reject'")
