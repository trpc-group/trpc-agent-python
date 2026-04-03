# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent cancellation event for TRPC Agent framework."""

from typing import Optional

from ._event import Event


class AgentCancelledEvent(Event):
    """Represents an agent run that was cancelled by user request.

    This event is yielded when a run is cancelled at a checkpoint,
    indicating that the agent execution was stopped cooperatively.

    Attributes:
        error_code: Set to "run_cancelled" to indicate cancellation.
        error_message: The reason for cancellation.
        invocation_id: The invocation ID of the cancelled run.
        author: The agent that was running when cancelled.
        branch: The branch of the event (optional).
    """

    def __init__(
        self,
        invocation_id: str,
        author: str,
        reason: str = "Run cancelled by user",
        branch: Optional[str] = None,
        **kwargs,
    ):
        """Initialize an AgentCancelledEvent.

        Args:
            invocation_id: The invocation ID of the cancelled run.
            author: The agent that was running when cancelled.
            reason: The cancellation reason (default: "Run cancelled by user").
            branch: The branch of the event (optional).
            **kwargs: Additional keyword arguments passed to Event.
        """
        super().__init__(invocation_id=invocation_id,
                         author=author,
                         error_code="run_cancelled",
                         error_message=reason,
                         branch=branch,
                         **kwargs)

    def model_post_init(self, __context):
        """Post initialization logic for the event."""
        # Call parent's post_init first
        super().model_post_init(__context)

        # Set error_code and error_message if not already set
        if not self.error_code:
            self.error_code = "run_cancelled"
        if not self.error_message:
            self.error_message = "Run cancelled by user"
