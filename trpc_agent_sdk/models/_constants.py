# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Module containing constants used across the model implementations.

This module defines string constants that are used as:
- Configuration keys
- API parameter names
- Response field names
"""

# Configuration related constants
API_KEY: str = 'api_key'
"""API key configuration parameter name."""

BASE_URL: str = 'base_url'
"""Base URL configuration parameter name."""

CLIENT_ARGS: str = 'client_args'
"""Client arguments configuration parameter name."""

ORGANIZATION: str = 'organization'
"""Organization identifier parameter name."""

# Request/Response field constants
ROLE: str = 'role'
"""Role field name in message objects."""

USER: str = 'user'
"""User field name in message objects."""

ASSISTANT: str = 'assistant'
"""Assistant field name in message objects."""

SYSTEM: str = 'system'
"""System field name in message objects."""

MODEL: str = 'model'
"""Model field name in API requests."""

TOOL: str = 'tool'
"""Tool field name in message objects."""

CONTENT: str = 'content'
"""Content field name in message objects."""

CHOICES: str = 'choices'
"""Choices field name in API responses."""

USAGE: str = 'usage'
"""Usage statistics field name in API responses."""

MESSAGE: str = 'message'
"""Message field name in API responses."""

TOOL_CALLS: str = 'tool_calls'
"""Tool calls field name in API responses."""

TOOL_CALL_ID: str = 'tool_call_id'
"""Tool call ID field name in API responses."""

DELTA: str = 'delta'
"""Delta field name in streaming responses."""

INDEX: str = 'index'
"""Index field name in API responses."""

RAW_RESPONSE: str = 'raw_response'
"""Raw response field name."""

FINISH_REASON: str = 'finish_reason'
"""Finish reason field name in API responses."""

CHUNK: str = 'chunk'
"""Chunk field name in streaming responses."""

TOOL_STREAMING: str = 'tool_streaming'
"""Tool streaming mode indicator name."""

REASONING_CONTENT: str = 'reasoning_content'
"""Reasoning content field name."""

# thinking
THINKING_ENABLED: str = "thinking_enabled"
"""thinking enabled indicator name."""

THINKING_TOKENS: str = "thinking_tokens"
"""Thinking tokens field"""

TOOL_STREAMING_ARGS: str = "tool_streaming_args"
"""Streaming tool call arguments delta key name."""
