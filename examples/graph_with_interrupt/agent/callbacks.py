# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Placeholder callbacks for graph_with_interrupt example."""

from trpc_agent_sdk.dsl.graph import NodeCallbacks


def create_node_callbacks() -> NodeCallbacks:
    """Return empty callbacks as a placeholder extension point."""
    return NodeCallbacks()
