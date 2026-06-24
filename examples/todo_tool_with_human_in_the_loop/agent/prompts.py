# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Prompts for the TodoWrite + Human-in-the-Loop demo agent."""

# Demo-specific persona only. ``DEFAULT_TODO_PROMPT`` is injected automatically
# by ``TodoWriteTool.process_request`` when the tool is registered on the agent.
INSTRUCTION = ("You are a rigorous engineering assistant that breaks a task and works "
               "through it step by step.\n")