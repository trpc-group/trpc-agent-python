# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
# Below code are copy and modified from https://github.com/ag-ui-protocol/ag-ui.git
#
# MIT License
#
# Copyright (c) 2025
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
"""Execution state management for background TRPC runs with tool support."""

import asyncio
import time
from typing import Set

from trpc_agent_sdk.log import logger


class ExecutionState:
    """Manages the state of a background TRPC execution.

    This class tracks:
    - The background asyncio task running the TRPC agent
    - Event queue for streaming results to the client
    - Execution timing and completion state
    """

    def __init__(self, task: asyncio.Task, thread_id: str, event_queue: asyncio.Queue):
        """Initialize execution state.

        Args:
            task: The asyncio task running the TRPC agent
            thread_id: The thread ID for this execution
            event_queue: Queue containing events to stream to client
        """
        self.task = task
        self.thread_id = thread_id
        self.event_queue = event_queue
        self.start_time = time.time()
        self.is_complete = False
        self.pending_tool_calls: Set[str] = set()  # Track outstanding tool call IDs for HITL

        logger.debug("Created execution state for thread %s", thread_id)

    def is_stale(self, timeout_seconds: int) -> bool:
        """Check if this execution has been running too long.

        Args:
            timeout_seconds: Maximum execution time in seconds

        Returns:
            True if execution has exceeded timeout
        """
        return time.time() - self.start_time > timeout_seconds

    async def cancel(self):
        """Cancel the execution and clean up resources."""
        logger.info("Cancelling execution for thread %s", self.thread_id)

        # Cancel the background task
        if not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

        self.is_complete = True

    def get_execution_time(self) -> float:
        """Get the total execution time in seconds.

        Returns:
            Time in seconds since execution started
        """
        return time.time() - self.start_time

    def add_pending_tool_call(self, tool_call_id: str):
        """Add a tool call ID to the pending set.

        Args:
            tool_call_id: The tool call ID to track
        """
        self.pending_tool_calls.add(tool_call_id)
        logger.debug("Added pending tool call %s to thread %s", tool_call_id, self.thread_id)

    def remove_pending_tool_call(self, tool_call_id: str):
        """Remove a tool call ID from the pending set.

        Args:
            tool_call_id: The tool call ID to remove
        """
        self.pending_tool_calls.discard(tool_call_id)
        logger.debug("Removed pending tool call %s from thread %s", tool_call_id, self.thread_id)

    def has_pending_tool_calls(self) -> bool:
        """Check if there are outstanding tool calls waiting for responses.

        Returns:
            True if there are pending tool calls (HITL scenario)
        """
        return len(self.pending_tool_calls) > 0

    def get_status(self) -> str:
        """Get a human-readable status of the execution.

        Returns:
            Status string describing the current state
        """
        if self.is_complete:
            if self.has_pending_tool_calls():
                return "complete_awaiting_tools"
            else:
                return "complete"
        elif self.task.done():
            return "task_done"
        else:
            return "running"

    def __repr__(self) -> str:
        """String representation of the execution state."""
        return (f"ExecutionState(thread_id='{self.thread_id}', "
                f"status='{self.get_status()}', "
                f"runtime={self.get_execution_time():.1f}s)")
