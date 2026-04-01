# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Node helpers and routing functions for generated graph workflow."""

from typing import Any

from .state import WorkflowState

NODE_ID_START = 'start'
NODE_ID_CODE1 = 'python_analysis'
NODE_ID_CODE2 = 'bash_system_info'
NODE_ID_CUSTOM_FORMAT_RESULTS1 = 'format_results'


async def node_start(state: WorkflowState) -> dict[str, Any]:
    return {}


async def node_custom_format_results1(state: WorkflowState) -> dict[str, Any]:
    # TODO: implement custom node logic for 'custom.format_results'.
    return {}
