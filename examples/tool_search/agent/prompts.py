# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Prompt for the real tool-safety agent demo."""

from trpc_agent_sdk.tools import TOOL_SEARCH_PLACEHOLDER

INSTRUCTION = f"""
Use tool_search before calling a deferred tool. If the catalog already shows
an exact name, copy that name into tool_names. A namespace only filters the
search scope and is not part of the tool name: use "get_forecast", not
"weather.get_forecast". Otherwise, search the matching namespace.

{TOOL_SEARCH_PLACEHOLDER}
"""
