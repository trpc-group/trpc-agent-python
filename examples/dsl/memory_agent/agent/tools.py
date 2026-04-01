# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tool helpers for generated graph workflow."""

from typing import Any

from trpc_agent_sdk.tools import load_memory_tool


def create_tools_llmagent1() -> list[Any]:
    tools: list[Any] = []
    tools.append(load_memory_tool)
    return tools
