# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""State schema for the graph example."""
from typing import Any
from typing_extensions import Annotated

from trpc_agent_sdk.dsl.graph import State
from trpc_agent_sdk.dsl.graph import append_list


class DocumentState(State):
    """Custom state for the minimal graph workflow."""

    document: str
    word_count: int
    route: str
    preview: str
    subgraph_reply: str
    query_reply: str
    context_note: str

    node_execution_history: Annotated[list[dict[str, Any]], append_list]
