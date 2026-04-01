# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Node helpers and routing functions for generated graph workflow."""

from typing import Any

from trpc_agent_sdk.dsl.graph import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph import STATE_KEY_NODE_RESPONSES
from trpc_agent_sdk.dsl.graph import STATE_KEY_USER_INPUT

from .state import WorkflowState

NODE_ID_START = 'start'
NODE_ID_TRANSFORM1 = 'build_message'
NODE_ID_LLMAGENT1 = 'echo_agent'
NODE_ID_END1 = 'end'


async def node_start(state: WorkflowState) -> dict[str, Any]:
    return {}


def map_input_llmagent1(state: WorkflowState) -> dict[str, Any]:
    child_state = dict(state)
    child_state[STATE_KEY_USER_INPUT] = str(
        state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM1]['overridden_user_message'])
    return child_state


async def node_transform1(state: WorkflowState) -> dict[str, Any]:
    value = {
        "original_user_input": state[STATE_KEY_USER_INPUT],
        "overridden_user_message": "OVERRIDDEN USER MESSAGE: [" + state[STATE_KEY_USER_INPUT] + "]"
    }
    return {STATE_KEY_NODE_RESPONSES: {NODE_ID_TRANSFORM1: value}}


async def node_end1(state: WorkflowState) -> dict[str, Any]:
    value = {
        "original_user_input": state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM1]['original_user_input'],
        "overridden_user_message": state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM1]['overridden_user_message'],
        "agent_output": str(state[STATE_KEY_NODE_RESPONSES][NODE_ID_LLMAGENT1])
    }
    last_response = value

    return {
        STATE_KEY_NODE_RESPONSES: {
            NODE_ID_END1: value
        },
        STATE_KEY_LAST_RESPONSE: last_response,
    }
