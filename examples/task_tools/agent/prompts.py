# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Prompts for the Task tools demo agent."""

# Demo-specific persona only. ``DEFAULT_TASK_PROMPT`` is injected automatically
# by the task tools' ``process_request`` when the toolset is registered.
INSTRUCTION = """You are a rigorous engineering assistant that breaks a project and works through it step by step. """
