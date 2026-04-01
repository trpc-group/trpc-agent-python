# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Placeholder callbacks for generated graph workflow."""

from trpc_agent_sdk.dsl.graph import NodeCallbacks


def create_node_callbacks() -> NodeCallbacks:
    """Return empty callbacks as a placeholder extension point."""
    return NodeCallbacks()
