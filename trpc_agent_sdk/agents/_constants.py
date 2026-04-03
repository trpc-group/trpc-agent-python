# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent System Constants Definition Module.

This module centralizes all agent-related constants following these principles:
1. Group related constants logically
2. Provide clear documentation for each constant
3. Maintain consistent naming conventions
4. Support both direct usage and type checking

Constants are categorized into:
- Filter naming conventions
- Model configuration defaults
- Type system mappings
- Context storage keys
"""

# Number of parts to split filter names into
FILTER_NAME_SPLIT_NUM = 2
"""Number of segments expected when parsing hierarchical filter names."""

MODEL_NAME = "model_name"
"""Default model name used by the agent."""

TOOL_CALL_INFO = "tool_call_info"
"""Key for storing tool call information in the context."""

TYPE_LABELS = {
    "STRING": "string",
    "NUMBER": "number",
    "BOOLEAN": "boolean",
    "OBJECT": "object",
    "ARRAY": "array",
    "INTEGER": "integer",
}
"""Canonical type labels for agent type system validation.

Maps internal type identifiers to standardized type names used in:
- API documentation
- Error messages
- Schema validation
"""
