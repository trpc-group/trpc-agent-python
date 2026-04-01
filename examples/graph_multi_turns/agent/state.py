# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""State schema for graph multi-turn example."""
from typing import Any
from typing_extensions import Annotated

from trpc_agent_sdk.dsl.graph import State
from trpc_agent_sdk.dsl.graph import append_list


class MultiTurnState(State):
    """Custom state for multi-turn branch selection demo."""

    query_text: str
    route: str
    llm_reply: str
    agent_reply: str
    context_note: str

    node_execution_history: Annotated[list[dict[str, Any]], append_list]
