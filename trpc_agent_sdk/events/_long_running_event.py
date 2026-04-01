# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Long Running Event."""

from trpc_agent_sdk.events._event import Event
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse


class LongRunningEvent(Event):
    """Represents a long-running event that requires human intervention.

    This event is used to pause agent execution and wait for human input
    or external processing to complete. It stores the function call and
    response for resumption.

    Attributes:
        function_call: The function call that triggered the long-running operation.
        function_response: The response from the long-running function.
    """

    function_call: FunctionCall
    """The function call that triggered the long-running operation."""
    function_response: FunctionResponse
    """The response from the long-running function."""

    def model_post_init(self, __context):
        """Post initialization logic for the event."""
        # Call parent's post_init first
        super().model_post_init(__context)

        # Set partial to True to indicate this is a partial response
        self.partial = True
