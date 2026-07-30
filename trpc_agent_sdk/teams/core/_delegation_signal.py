# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Delegation Signal Pydantic model for TeamAgent.

This module defines the DelegationSignal model that is returned by delegation
tools. TeamAgent detects this signal in function responses and handles member
execution accordingly.

The signal pattern allows TeamAgent to intercept delegation requests and manually
execute member agents, giving TeamAgent full control over the execution flow.

Note on serialization:
    When FunctionTool returns a Pydantic BaseModel, it's converted to JSON string
    via model_dump_json(). The ToolsProcessor then wraps it as {"result": "<json_string>"}.
    This module provides helper methods to detect and parse delegation signals from
    both dict and JSON string formats.
"""

from __future__ import annotations

import json
from typing import Any
from typing import Literal
from typing import Optional
from typing import Union

from pydantic import BaseModel

# Unique marker to identify delegation signals in tool responses
DELEGATION_SIGNAL_MARKER = "__TEAM_DELEGATION__"


class DelegationSignal(BaseModel):
    """Pydantic model returned by delegation tool, detected by TeamAgent.

    This signal pattern allows TeamAgent to intercept delegation requests
    and manually execute member agents, giving TeamAgent full control
    over the execution flow.

    Using BaseModel ensures proper serialization by the framework.
    The tool returns this model directly, and the framework serializes it
    to JSON string. TeamAgent then detects it in the function_response by
    checking for the marker field.

    Attributes:
        marker: Unique string marker for identifying delegation signals.
        action: The type of delegation action (to single member or all).
        member_name: Name of the member to delegate to (for single member).
        task: The task description for the member(s) to execute.
    """

    marker: str = DELEGATION_SIGNAL_MARKER
    action: Literal["delegate_to_member", "delegate_to_all"] = "delegate_to_member"
    member_name: str = ""
    task: str = ""

    @classmethod
    def is_delegation_signal(cls, response_data: Union[dict, str, Any]) -> bool:
        """Check if response data is a delegation signal.

        Handles multiple formats:
        1. DelegationSignal instance - direct check
        2. dict with marker field - check marker value
        3. JSON string - parse and check marker value

        Args:
            response_data: Data from function response to check (dict, str, or any).

        Returns:
            True if the response contains the delegation signal marker.
        """
        # Already a DelegationSignal instance
        if isinstance(response_data, cls):
            return True

        # Dict format - check marker field
        if isinstance(response_data, dict):
            return response_data.get("marker") == DELEGATION_SIGNAL_MARKER

        # JSON string format - parse and check
        if isinstance(response_data, str):
            try:
                parsed = json.loads(response_data)
                if isinstance(parsed, dict):
                    return parsed.get("marker") == DELEGATION_SIGNAL_MARKER
            except (json.JSONDecodeError, TypeError):
                pass

        return False

    @classmethod
    def from_response(cls, response_data: Union[dict, str]) -> Optional[DelegationSignal]:
        """Create DelegationSignal from function response data.

        Handles multiple formats:
        1. dict - extract fields directly
        2. JSON string - parse then extract fields

        Args:
            response_data: Dictionary or JSON string containing signal data.

        Returns:
            DelegationSignal instance populated from the response data,
            or None if parsing fails.
        """
        # Parse JSON string if needed
        data = response_data
        if isinstance(response_data, str):
            try:
                data = json.loads(response_data)
            except (json.JSONDecodeError, TypeError):
                return None

        if not isinstance(data, dict):
            return None

        return cls(
            marker=data.get("marker", DELEGATION_SIGNAL_MARKER),
            action=data.get("action", "delegate_to_member"),
            member_name=data.get("member_name", ""),
            task=data.get("task", ""),
        )
