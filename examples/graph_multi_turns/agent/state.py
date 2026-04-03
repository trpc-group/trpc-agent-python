# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
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
