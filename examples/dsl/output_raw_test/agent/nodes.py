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
NODE_ID_TRANSFORM1 = 'classifier'
NODE_ID_TRANSFORM2 = 'handle_positive'
NODE_ID_TRANSFORM3 = 'handle_negative'
NODE_ID_TRANSFORM4 = 'handle_neutral'
NODE_ID_END1 = 'end'


async def node_start(state: WorkflowState) -> dict[str, Any]:
    return {}


async def node_transform1(state: WorkflowState) -> dict[str, Any]:
    value = ('positive' if state[STATE_KEY_USER_INPUT].__contains__('love')
             or state[STATE_KEY_USER_INPUT].__contains__('great') else
             ('negative' if state[STATE_KEY_USER_INPUT].__contains__('hate')
              or state[STATE_KEY_USER_INPUT].__contains__('terrible') else 'neutral'))
    return {STATE_KEY_NODE_RESPONSES: {NODE_ID_TRANSFORM1: value}}


async def node_transform2(state: WorkflowState) -> dict[str, Any]:
    value = {
        "sentiment": "positive",
        "message": "Great! The sentiment is positive.",
        "text_response": str(state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM1])
    }
    return {STATE_KEY_NODE_RESPONSES: {NODE_ID_TRANSFORM2: value}}


async def node_transform3(state: WorkflowState) -> dict[str, Any]:
    value = {
        "sentiment": "negative",
        "message": "The sentiment is negative.",
        "text_response": str(state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM1])
    }
    return {STATE_KEY_NODE_RESPONSES: {NODE_ID_TRANSFORM3: value}}


async def node_transform4(state: WorkflowState) -> dict[str, Any]:
    value = {
        "sentiment": "neutral",
        "message": "The sentiment is neutral.",
        "text_response": str(state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM1])
    }
    return {STATE_KEY_NODE_RESPONSES: {NODE_ID_TRANSFORM4: value}}


async def node_end1(state: WorkflowState) -> dict[str, Any]:
    value = (state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM2]
             if NODE_ID_TRANSFORM2 in state[STATE_KEY_NODE_RESPONSES] and
             (NODE_ID_TRANSFORM2 in state[STATE_KEY_NODE_RESPONSES]) else
             (state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM3]
              if NODE_ID_TRANSFORM3 in state[STATE_KEY_NODE_RESPONSES] and
              (NODE_ID_TRANSFORM3 in state[STATE_KEY_NODE_RESPONSES]) else
              state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM4]))
    last_response = value

    return {
        STATE_KEY_NODE_RESPONSES: {
            NODE_ID_END1: value
        },
        STATE_KEY_LAST_RESPONSE: last_response,
    }


def route_func1(state: WorkflowState) -> str:
    if str(state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM1]).__contains__('positive'):
        return NODE_ID_TRANSFORM2
    if str(state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM1]).__contains__('negative'):
        return NODE_ID_TRANSFORM3
    return NODE_ID_TRANSFORM4
