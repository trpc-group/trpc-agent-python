# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Placeholder callbacks for graph_multi_turns example."""

from trpc_agent_sdk.dsl.graph import NodeCallbacks


def create_node_callbacks() -> NodeCallbacks:
    """Return empty callbacks as a placeholder extension point."""
    return NodeCallbacks()
