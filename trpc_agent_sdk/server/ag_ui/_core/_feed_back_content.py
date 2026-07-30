# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Feedback content structure for user feedback handler."""

from __future__ import annotations

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import PrivateAttr
from trpc_agent_sdk.sessions import Session


class AgUiUserFeedBack(BaseModel):
    """Content provided to user feedback handler.

    This structure contains the session and tool result information
    that the user can inspect and modify in their feedback handler.

    Example:
        def my_feedback_handler(content: AgUiUserFeedBack):
            # Modify session state
            content.session.state['key'] = 'value'
            # Mark as modified so changes are saved
            content.mark_session_modified()

            # Optionally modify the tool message that will be sent to the agent
            content.tool_message = "modified content"

    Attributes:
        session: The TRPC Session object (can be modified)
        tool_name: Name of the tool that was called (read-only)
        tool_message: The tool result message/content from the user (can be modified)
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=True)

    session: Session = Field(description="The TRPC Session object")
    tool_name: str = Field(description="Name of the tool that was called", allow_mutation=False)
    tool_message: str = Field(description="The tool result message/content from the user")

    _session_modified: bool = PrivateAttr(default=False)

    def mark_session_modified(self) -> None:
        """Mark the session as modified.

        Call this method after modifying the session to indicate that
        changes need to be saved.
        """
        self._session_modified = True

    def check_session_modified(self) -> bool:
        """Check if the session has been modified.

        Returns:
            True if session has been modified, False otherwise
        """
        return self._session_modified
