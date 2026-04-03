# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Prompt definitions for graph multi-turn example."""

LLM_NODE_INSTRUCTION = """You are a concise assistant running inside a graph llm_node.
Use session context when the user references earlier turns.
Respond in 1-2 short sentences unless explicitly asked for more."""

AGENT_NODE_WORKER_INSTRUCTION = """You are branch_agent_worker, a sub-agent invoked by graph agent_node.
Use the ongoing conversation context and reply briefly in a friendly tone.
Start your answer with 'Agent branch:' for visibility."""
