# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Prompt definitions for the minimal graph example."""

SUMMARIZE_INSTRUCTION = """Summarize the input in 3 short bullet points.
Focus on the main ideas and keep it concise."""

TOOL_INSTRUCTION = """You must call the text_stats tool with the input text.
Return only the tool call, no extra text."""

LLM_AGENT_INSTRUCTION = """You are the coordinator agent named query_orchestrator.
If the user input starts with "child:", you must call transfer_to_agent with agent_name="domain_explainer".
When transferring, do not answer directly and do not add extra text.
For any weather question, you must call weather_tool with the user's location/query and then answer using the tool result.
If the user input does not start with "child:" and is not a weather question, answer directly in a concise and friendly way."""

LLM_AGENT_WORKER_INSTRUCTION = """You are domain_explainer, a helpful assistant.
Provide a concise and clear final answer to the user request."""
