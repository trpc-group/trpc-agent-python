# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""State schema for graph interrupt example."""
from typing import Any
from typing_extensions import Annotated

from trpc_agent_sdk.dsl.graph import State
from trpc_agent_sdk.dsl.graph import append_list


class InterruptState(State):
    """Custom state for interrupt approval flow."""

    request_text: str
    suggested_action: str
    approval_status: str
    approval_note: str
    summary_request: str
    approval_summary: str
    context_note: str

    node_execution_history: Annotated[list[dict[str, Any]], append_list]
