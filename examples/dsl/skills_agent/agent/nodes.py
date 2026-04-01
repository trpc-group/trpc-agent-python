# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Node helpers and routing functions for generated graph workflow."""

from typing import Any

from .state import WorkflowState

NODE_ID_START = 'start'
NODE_ID_LLMAGENT1 = 'agent'
NODE_ID_END1 = 'end'


async def node_start(state: WorkflowState) -> dict[str, Any]:
    return {}


async def node_end1(state: WorkflowState) -> dict[str, Any]:
    return {}
