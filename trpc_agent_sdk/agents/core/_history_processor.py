# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""History message control processor for TRPC Agent framework.

This module provides history filtering and processing logic for LlmAgent,
including timeline-based filtering, branch-based filtering, and role preservation.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger

# Constant key for storing calculated branch in custom_metadata
_TRPC_USER_MESSAGE_BRANCH = "_trpc_user_message_branch"


class TimelineFilterMode(str, Enum):
    """Timeline filter mode for conversation history.

    Controls which historical messages are included based on temporal scope.
    """

    ALL = "all"
    """Include all historical messages regardless of when they were created."""

    INVOCATION = "invocation"
    """Only include messages from the current invocation (same runner.run_async() call)."""


class BranchFilterMode(str, Enum):
    """Branch filter mode for conversation history.

    Controls which historical messages are included based on agent branch relationships.
    """

    PREFIX = "prefix"
    """Include messages where the event's branch is a prefix of the current branch."""

    ALL = "all"
    """Include messages from all branches regardless of relationship."""

    EXACT = "exact"
    """Only include messages from the exact same branch."""


class HistoryProcessor:
    """Processor for filtering and managing conversation history.

    This class encapsulates all history filtering logic including:
    - Timeline-based filtering (by invocation_id)
    - Branch-based filtering (by branch relationships)
    - Max history runs limiting
    """

    def __init__(
        self,
        max_history_messages: int = 0,
        timeline_filter_mode: TimelineFilterMode = TimelineFilterMode.ALL,
        branch_filter_mode: BranchFilterMode = BranchFilterMode.ALL,
    ):
        """Initialize HistoryProcessor with filtering parameters.

        Args:
            max_history_messages: Maximum number of history messages (0 = no limit)
            timeline_filter_mode: Timeline filter mode
            branch_filter_mode: Branch filter mode
        """
        self.max_history_messages = max_history_messages
        self.timeline_filter_mode = timeline_filter_mode
        self.branch_filter_mode = branch_filter_mode

    def filter_events(
        self,
        ctx: InvocationContext,
        events: list[Event],
    ) -> list[Event]:
        """Filter events based on history control policies.

        Applies filters in the following order:
        0. Tag user events with calculated branch
        1. Timeline filtering (by invocation_id)
        2. Branch filtering (by branch prefix/exact/all)
        3. Transfer-to-agent filtering (exclude transfer_to_agent events)
        4. Content filtering (non-empty content)
        5. Max history runs limiting (take last N messages)

        Note: The separate include_previous_history parameter has been removed.
        Use branch_filter_mode instead:
        - BranchFilterMode.ALL: Include all branches (equivalent to include_previous_history=True)
        - BranchFilterMode.EXACT: Only include same branch (equivalent to include_previous_history=False)
        - BranchFilterMode.PREFIX: Include ancestor/descendant branches

        Args:
            ctx: Invocation context
            events: List of events to filter

        Returns:
            Filtered list of events
        """
        current_branch = ctx.branch

        # Step 0: Tag user events with calculated branch (only for PREFIX/EXACT modes)
        need_calculate_branch = self._need_calculate_user_event_branch(self.branch_filter_mode)
        if need_calculate_branch:
            events = self._tag_user_events_with_calculated_branch(events)

        filtered_events = []
        for event in events:
            # Step 1: Timeline filtering
            if not self._should_include_event_by_timeline(event, self.timeline_filter_mode, ctx):
                continue

            # Step 2: Branch filtering
            if not self._should_include_event_by_branch(event, current_branch, self.branch_filter_mode):
                continue

            # Step 3: Filter out transfer_to_agent events
            if self._contains_transfer_to_agent(event):
                continue

            # Step 4: Content filtering
            if not self._should_include_event_in_contents(event):
                continue

            filtered_events.append(event)

        # Step 5: Max history runs limiting (applied last)
        if self.max_history_messages > 0 and len(filtered_events) > self.max_history_messages:
            logger.debug("Limiting history from %s to %s events", len(filtered_events), self.max_history_messages)

            # Check if the first event after limiting contains function_response from current agent
            first_element = filtered_events[-self.max_history_messages:][0]
            if (first_element.branch == ctx.branch and first_element.content and first_element.content.parts
                    and any(part.function_response for part in first_element.content.parts)):
                # Include one more event to get the corresponding function_call
                filtered_events = filtered_events[-(self.max_history_messages + 1):]
                logger.debug("Added previous event with function_call to maintain continuity")
            else:
                filtered_events = filtered_events[-self.max_history_messages:]

        # Step 6: Clean up calculated branch from custom_metadata (only if calculation was performed)
        if need_calculate_branch:
            self._cleanup_calculated_branch(filtered_events)

        return filtered_events

    def _should_include_event_by_timeline(
        self,
        event: Event,
        timeline_filter_mode: TimelineFilterMode,
        ctx: Optional[InvocationContext],
    ) -> bool:
        """Determine if an event should be included based on timeline filtering.

        Args:
            event: The event to check
            timeline_filter_mode: The timeline filter mode to apply
            ctx: The invocation context (for invocation_id)

        Returns:
            True if the event should be included, False otherwise
        """
        if timeline_filter_mode == TimelineFilterMode.ALL:
            return True

        # INVOCATION mode: Filter by invocation_id (which represents a single runner.run_async() call)
        if timeline_filter_mode == TimelineFilterMode.INVOCATION:
            if ctx and event.invocation_id:
                return event.invocation_id == ctx.invocation_id
            # If no invocation_id available, include by default
            return True

        # Unknown mode, include by default
        logger.warning("Unknown timeline filter mode: %s, including event", timeline_filter_mode)
        return True

    def _should_include_event_by_branch(
        self,
        event: Event,
        current_branch: Optional[str],
        branch_filter_mode: BranchFilterMode,
    ) -> bool:
        """Determine if an event should be included based on branch filtering.

        Args:
            event: The event to check
            current_branch: The current agent's branch
            branch_filter_mode: The branch filter mode to apply

        Returns:
            True if the event should be included, False otherwise
        """
        if branch_filter_mode == BranchFilterMode.ALL:
            return True

        # Special handling for user events with calculated branch
        if event.author == "user" and event.custom_metadata and _TRPC_USER_MESSAGE_BRANCH in event.custom_metadata:
            calculated_branch = event.custom_metadata[_TRPC_USER_MESSAGE_BRANCH]
            # For PREFIX and EXACT modes, user event is included only when
            # the calculated branch equals the current agent's branch
            if branch_filter_mode == BranchFilterMode.EXACT:
                return calculated_branch == current_branch
            elif branch_filter_mode == BranchFilterMode.PREFIX:
                return (calculated_branch == current_branch or current_branch.startswith(calculated_branch + "."))

        # If no branch information, include by default
        if not current_branch or not event.branch:
            return True

        if branch_filter_mode == BranchFilterMode.EXACT:
            return event.branch == current_branch

        if branch_filter_mode == BranchFilterMode.PREFIX:
            # Include if event's branch is a prefix of current branch (ancestor or self)
            # Examples:
            # - event.branch="coordinator" matches current_branch="coordinator.math_agent" (ancestor)
            # - event.branch="coordinator" matches current_branch="coordinator" (self)
            # - event.branch="coordinator.info_agent" does NOT match current_branch="coordinator.math_agent" (sibling)
            #
            # Must be exact match OR followed by a dot separator to avoid partial matches
            return (event.branch == current_branch or current_branch.startswith(event.branch + "."))

        # Unknown mode, fall back to prefix matching
        logger.warning("Unknown branch filter mode: %s, using prefix mode", branch_filter_mode)
        return (event.branch == current_branch or current_branch.startswith(event.branch + "."))

    def _should_include_event_in_contents(self, event: Event) -> bool:
        """Determine if an event should be included in contents.

        Filters out events with empty content.

        Args:
            event: The event to check

        Returns:
            True if the event should be included, False otherwise
        """
        # Don't include events without content
        if not event.content:
            return False

        # Don't include events without parts
        if not event.content.parts:
            return False

        return True

    def _contains_transfer_to_agent(self, event: Event) -> bool:
        """Check if event contains transfer_to_agent function calls/responses.

        Args:
            event: The event to check

        Returns:
            True if the event contains transfer_to_agent, False otherwise
        """
        if not event.content or not event.content.parts:
            return False

        for part in event.content.parts:
            if part.function_call and part.function_call.name == "transfer_to_agent":
                return True
            if part.function_response and part.function_response.name == "transfer_to_agent":
                return True

        return False

    def _need_calculate_user_event_branch(self, branch_filter_mode: BranchFilterMode) -> bool:
        """Determine if user event branch calculation is needed.

        Branch calculation is only needed for PREFIX and EXACT filter modes,
        as these modes require knowing which branch user events belong to.

        Args:
            branch_filter_mode: The branch filter mode being used

        Returns:
            True if branch calculation is needed, False otherwise
        """
        return branch_filter_mode in (BranchFilterMode.PREFIX, BranchFilterMode.EXACT)

    def _tag_user_events_with_calculated_branch(self, events: list[Event]) -> list[Event]:
        """Tag user events with calculated branch based on agent execution path.

        For each Event(author=user), the calculated branch is the concatenation of agent names
        that executed between this user event and the next user event, formatted as
        "agent1_name.agent2_name".

        If there is no next user event, the current user event should always be included
        (no calculated branch needed).

        Args:
            events: List of events to process

        Returns:
            List of events with user events tagged with calculated branch in custom_metadata
        """
        if not events:
            return events

        # Find all user event indices
        user_event_indices = [i for i, event in enumerate(events) if event.author == "user"]

        if not user_event_indices:
            return events

        # Process each user event
        for idx, user_idx in enumerate(user_event_indices):
            # Check if there's a next user event
            if idx + 1 < len(user_event_indices):
                next_user_idx = user_event_indices[idx + 1]

                # Collect agent names between this user event and the next user event
                agent_names = []
                for i in range(user_idx + 1, next_user_idx):
                    event = events[i]
                    # Only include events from agents (not user)
                    if event.author != "user" and event.author:
                        # Only add if not already in the list to avoid duplicates
                        if event.author not in agent_names:
                            agent_names.append(event.author)

                # Create calculated branch
                if agent_names:
                    calculated_branch = ".".join(agent_names)

                    # Tag the user event with calculated branch in custom_metadata
                    user_event = events[user_idx]
                    if user_event.custom_metadata is None:
                        user_event.custom_metadata = {}
                    user_event.custom_metadata[_TRPC_USER_MESSAGE_BRANCH] = calculated_branch
                    logger.debug("Tagged user event at index %s with calculated branch: %s", user_idx,
                                 calculated_branch)
            # else: If there's no next user event, this event should always be included (no tagging needed)

        return events

    def _cleanup_calculated_branch(self, events: list[Event]) -> None:
        """Remove calculated branch from custom_metadata after filtering.

        This cleanup method removes the _TRPC_USER_MESSAGE_BRANCH key from
        custom_metadata of all events after the filtering process is complete.

        Args:
            events: List of events to cleanup
        """
        for event in events:
            if event.custom_metadata and _TRPC_USER_MESSAGE_BRANCH in event.custom_metadata:
                del event.custom_metadata[_TRPC_USER_MESSAGE_BRANCH]
                logger.debug("Removed calculated branch from user event %s", event.id)
