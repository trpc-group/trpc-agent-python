# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Node helpers and routing functions for generated graph workflow."""

from typing import Any

from trpc_agent_sdk.dsl.graph import STATE_KEY_NODE_RESPONSES
from trpc_agent_sdk.dsl.graph import STATE_KEY_USER_INPUT

from .state import WorkflowState

NODE_ID_START = 'start'
NODE_ID_SET_STATE1 = 'save_question'
NODE_ID_LLMAGENT1 = 'query_rewriter'
NODE_ID_KNOWLEDGE_SEARCH1 = 'knowledge_search'
NODE_ID_TRANSFORM1 = 'format_docs'
NODE_ID_LLMAGENT2 = 'answer_generator'
NODE_ID_END1 = 'end'


async def node_start(state: WorkflowState) -> dict[str, Any]:
    return {}


def map_input_llmagent1(state: WorkflowState) -> dict[str, Any]:
    child_state = dict(state)
    child_state['user_question'] = state['user_question']
    return child_state


def map_input_llmagent2(state: WorkflowState) -> dict[str, Any]:
    child_state = dict(state)
    child_state['doc_count'] = state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM1]['doc_count']
    child_state['score_1'] = state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM1]['score_1']
    child_state['doc_1'] = state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM1]['doc_1']
    child_state['doc_2'] = state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM1]['doc_2']
    child_state['doc_3'] = state[STATE_KEY_NODE_RESPONSES][NODE_ID_TRANSFORM1]['doc_3']
    child_state['user_question'] = state['user_question']
    return child_state


async def node_transform1(state: WorkflowState) -> dict[str, Any]:
    value = {
        "doc_count":
        len(state[STATE_KEY_NODE_RESPONSES][NODE_ID_KNOWLEDGE_SEARCH1]['documents']),
        "doc_1": (state[STATE_KEY_NODE_RESPONSES][NODE_ID_KNOWLEDGE_SEARCH1]['documents'][0]['text']
                  if len(state[STATE_KEY_NODE_RESPONSES][NODE_ID_KNOWLEDGE_SEARCH1]['documents']) > 0 else ""),
        "doc_2": (state[STATE_KEY_NODE_RESPONSES][NODE_ID_KNOWLEDGE_SEARCH1]['documents'][1]['text']
                  if len(state[STATE_KEY_NODE_RESPONSES][NODE_ID_KNOWLEDGE_SEARCH1]['documents']) > 1 else ""),
        "doc_3": (state[STATE_KEY_NODE_RESPONSES][NODE_ID_KNOWLEDGE_SEARCH1]['documents'][2]['text']
                  if len(state[STATE_KEY_NODE_RESPONSES][NODE_ID_KNOWLEDGE_SEARCH1]['documents']) > 2 else ""),
        "score_1": (state[STATE_KEY_NODE_RESPONSES][NODE_ID_KNOWLEDGE_SEARCH1]['documents'][0]['score']
                    if len(state[STATE_KEY_NODE_RESPONSES][NODE_ID_KNOWLEDGE_SEARCH1]['documents']) > 0 else 0.0)
    }
    return {STATE_KEY_NODE_RESPONSES: {NODE_ID_TRANSFORM1: value}}


async def node_set_state1(state: WorkflowState) -> dict[str, Any]:
    return {
        'user_question': state[STATE_KEY_USER_INPUT],
    }


async def node_end1(state: WorkflowState) -> dict[str, Any]:
    return {}


def resolve_query_knowledge_search1(state: WorkflowState) -> str:
    return str(state[STATE_KEY_NODE_RESPONSES][NODE_ID_LLMAGENT1]['search_query'])
