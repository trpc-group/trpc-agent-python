# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Module containing constants used across the model implementations.

This module defines string constants that are used as:
- Configuration keys
- API parameter names
- Response field names
"""

TOOL_CONTEXT: str = "tool_context"
"""Key for storing tool context in the request context."""

INPUT_STREAM = "input_stream"
"""Key for storing input stream in the request context."""

DEFAULT_API_VARIANT = "default"
"""Default value for optional parameters."""

DEFAULT_TOOLSET_NAME = "default"
"""Default value for optional parameters."""

TOOL_NAME = "set_model_response"
"""Tool name for setting model response."""
