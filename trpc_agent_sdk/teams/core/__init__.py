# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Team Core module for TeamAgent implementation.

This module provides the core components for TeamAgent functionality:
- DelegationSignal: Pydantic model for delegation tool responses
- TeamRunContext: Runtime context for tracking member interactions (state-based)
- TeamMessageBuilder: Message builder for member agent context
- Delegation tools: Tools for delegating tasks to members
- System message generator: Creates leader system messages
"""

from ._delegation_signal import DELEGATION_SIGNAL_MARKER
from ._delegation_signal import DelegationSignal
from ._delegation_tools import DELEGATE_TOOL_NAME
from ._delegation_tools import create_delegate_to_member_tool
from ._member_message_filter import TeamMemberMessageFilter
from ._member_message_filter import keep_all_member_message
from ._member_message_filter import keep_last_member_message
from ._message_builder import TeamMessageBuilder
from ._system_message import generate_team_leader_system_message
from ._system_message import get_member_info_list
from ._team_run_context import TEAM_STATE_KEY
from ._team_run_context import TeamRunContext

__all__ = [
    "DELEGATION_SIGNAL_MARKER",
    "DelegationSignal",
    "DELEGATE_TOOL_NAME",
    "create_delegate_to_member_tool",
    "TeamMemberMessageFilter",
    "keep_all_member_message",
    "keep_last_member_message",
    "TeamMessageBuilder",
    "generate_team_leader_system_message",
    "get_member_info_list",
    "TEAM_STATE_KEY",
    "TeamRunContext",
]
