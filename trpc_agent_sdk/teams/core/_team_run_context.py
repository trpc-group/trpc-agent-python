# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Team Run Context for tracking member interactions during team execution.

This module provides the TeamRunContext dataclass that tracks member interactions
during a single team run, enabling history sharing between members if configured.

The TeamRunContext is stored in session.state and updated via state_delta events.
Each history entry includes an invocation_id to track run boundaries for limiting
history to the last N runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Set

# Key for team context in session.state
TEAM_STATE_KEY = "_team_context"


@dataclass
class TeamRunContext:
    """Runtime context for team execution.

    Tracks member interactions during a single team run,
    enabling history sharing between members if configured.

    The context is stored in session.state under TEAM_STATE_KEY and
    updated via event.actions.state_delta for persistence.

    Each history entry includes an invocation_id for run boundary tracking,
    enabling limiting history to the last N runs (invocations).

    Attributes:
        interactions: List of recorded member interactions with task and response.
        team_name: Name of the team for metadata tracking.
        leader_history: List of text-only conversation history for the leader.
            Each entry has {"role": "user"|"model", "text": "...", "invocation_id": "..."}
        current_invocation_id: The current invocation ID for tracking run boundaries.
        pending_function_call_id: ID of pending human-in-the-loop function call.
    """

    interactions: List[Dict[str, str]] = field(default_factory=list)
    """List of member interactions in order of execution.
    Each entry: {"member": str, "task": str, "response": str, "invocation_id": str}
    """

    team_name: str = ""
    """Name of the team for metadata tracking."""

    leader_history: List[Dict[str, str]] = field(default_factory=list)
    """Text-only conversation history for the leader.
    Each entry: {"role": "user"|"model", "text": "...", "invocation_id": "..."}
    Delegation tool calls are converted to text format.
    """

    current_invocation_id: str = ""
    """The current invocation ID for tracking run boundaries."""

    # Human-in-the-loop pending state
    pending_function_call_id: str = ""
    """ID of the pending function call awaiting human input (leader only)."""

    def add_interaction(self, member_name: str, task: str, response: str) -> None:
        """Record a member interaction.

        Args:
            member_name: Name of the member agent.
            task: The task that was assigned to the member.
            response: The response from the member agent.
        """
        self.interactions.append({
            'member': member_name,
            'task': task,
            'response': response,
            'invocation_id': self.current_invocation_id,
        })

    def add_leader_message(self, role: str, text: str) -> None:
        """Add a message to leader's conversation history.

        Args:
            role: "user" or "model"
            text: The text content of the message
        """
        if text.strip():  # Only add non-empty messages
            self.leader_history.append({
                'role': role,
                'text': text,
                'invocation_id': self.current_invocation_id,
            })

    def add_delegation_record(self, member_name: str, task: str, response: str) -> None:
        """Add a delegation record to leader's history as text.

        Converts the delegation tool call/response to a text format for the leader.
        The member's response is wrapped in <member_interaction_context> tags
        to clearly delineate the member's contribution.

        Args:
            member_name: Name of the member agent delegated to.
            task: The task that was delegated.
            response: The response from the member agent.
        """
        delegation_text = (f"I have delegated task to '{member_name}':\n"
                           f"<member_interaction_context>\n"
                           f"Task: {task}\n"
                           f"Response: {response}\n"
                           f"</member_interaction_context>\n")
        self.leader_history.append({
            'role': 'model',
            'text': delegation_text,
            'invocation_id': self.current_invocation_id,
        })

    # Human-in-the-loop (HITL) methods

    def set_pending_hitl(self, function_call_id: str) -> None:
        """Record pending human-in-the-loop state.

        Called when the leader agent triggers a LongRunningEvent that requires
        human input before the team can continue.

        Args:
            function_call_id: ID of the function call awaiting human input.
        """
        self.pending_function_call_id = function_call_id

    def clear_pending_hitl(self) -> None:
        """Clear pending human-in-the-loop state after resume."""
        self.pending_function_call_id = ""

    def add_cancellation_record(self, cancelled_during: str = "") -> None:
        """Add a cancellation record to leader's history.

        Called when the team execution is cancelled to record the cancellation
        in the leader's conversation history for context in future interactions.

        Args:
            cancelled_during: Description of what was happening when cancelled
                            (e.g., "delegation to researcher", "leader thinking")
        """
        cancel_text = "User cancelled the agent execution."
        if cancelled_during:
            cancel_text = f"User cancelled the agent execution during {cancelled_during}."

        self.leader_history.append({
            'role': 'model',
            'text': cancel_text,
            'invocation_id': self.current_invocation_id,
        })

    def has_pending_hitl(self) -> bool:
        """Check if there's a pending human-in-the-loop event.

        Returns:
            True if there's a pending HITL event awaiting human input.
        """
        return bool(self.pending_function_call_id)

    def get_leader_history_for_runs(self, num_runs: int) -> List[Dict[str, str]]:
        """Get leader history limited to the last N invocations.

        Args:
            num_runs: Number of past invocations to include. If <= 0, returns empty list.

        Returns:
            Filtered history entries from the last N invocations.
        """
        if not self.leader_history or num_runs <= 0:
            return []

        # Get unique invocation IDs in order of first occurrence
        seen_invocations: List[str] = []
        for entry in self.leader_history:
            inv_id = entry.get('invocation_id', '')
            if inv_id and inv_id not in seen_invocations:
                seen_invocations.append(inv_id)

        # If no invocation IDs found (legacy data), return all history
        if not seen_invocations:
            return self.leader_history.copy()

        # Get the last N invocation IDs
        allowed_invocations: Set[str] = set(seen_invocations[-num_runs:])

        # Filter history - include entries without invocation_id (legacy)
        # plus entries from allowed invocations
        return [
            entry for entry in self.leader_history
            if not entry.get('invocation_id') or entry.get('invocation_id') in allowed_invocations
        ]

    def get_current_run_interactions(self) -> List[Dict[str, str]]:
        """Get interactions from the current invocation only.

        Returns:
            List of interactions from the current invocation.
        """
        if not self.current_invocation_id:
            return self.interactions.copy()

        return [entry for entry in self.interactions if entry.get('invocation_id') == self.current_invocation_id]

    def get_member_interactions_for_runs(self, member_name: str, num_runs: int) -> List[Dict[str, str]]:
        """Get one member's interactions limited to the last N invocations.

        Args:
            member_name: Name of the member to retrieve interactions for.
            num_runs: Number of past invocations to include. If <= 0, returns empty list.

        Returns:
            Filtered interactions for the member from the last N invocations.
        """
        if not member_name or not self.interactions or num_runs <= 0:
            return []

        # Get unique invocation IDs in order of first occurrence
        seen_invocations: List[str] = []
        for entry in self.interactions:
            inv_id = entry.get('invocation_id', '')
            if inv_id and inv_id not in seen_invocations:
                seen_invocations.append(inv_id)

        # If no invocation IDs found (legacy data), return all member interactions
        if not seen_invocations:
            return [entry for entry in self.interactions if entry.get('member') == member_name]

        # Get the last N invocation IDs
        allowed_invocations: Set[str] = set(seen_invocations[-num_runs:])

        # Filter member interactions - include entries without invocation_id (legacy)
        # plus entries from allowed invocations
        return [
            entry for entry in self.interactions if entry.get('member') == member_name and (
                not entry.get('invocation_id') or entry.get('invocation_id') in allowed_invocations)
        ]

    def clear(self) -> None:
        """Clear all recorded interactions, history, and pending HITL state."""
        self.interactions.clear()
        self.leader_history.clear()
        self.clear_pending_hitl()

    def to_state_dict(self) -> Dict[str, Any]:
        """Serialize TeamRunContext to a dictionary for session.state.

        Returns:
            Dictionary representation of the context.
        """
        return {
            'interactions': self.interactions.copy(),
            'team_name': self.team_name,
            'leader_history': self.leader_history.copy(),
            'current_invocation_id': self.current_invocation_id,
            # Human-in-the-loop pending state
            'pending_function_call_id': self.pending_function_call_id,
        }

    @classmethod
    def from_state(cls, state: Dict[str, Any], team_name: str = "") -> "TeamRunContext":
        """Restore TeamRunContext from session.state.

        Args:
            state: The session.state dictionary.
            team_name: Name of the team (used if not in state).

        Returns:
            TeamRunContext restored from state, or new empty context if not found.
        """
        team_state = state.get(TEAM_STATE_KEY)
        if not team_state:
            return cls(team_name=team_name)

        return cls(
            interactions=team_state.get('interactions', []).copy(),
            team_name=team_state.get('team_name', team_name),
            leader_history=team_state.get('leader_history', []).copy(),
            current_invocation_id=team_state.get('current_invocation_id', ''),
            # Human-in-the-loop pending state
            pending_function_call_id=team_state.get('pending_function_call_id', ''),
        )

    def get_state_delta(self) -> Dict[str, Any]:
        """Get state_delta dict for updating session.state.

        Returns:
            Dictionary with TEAM_STATE_KEY mapped to current context state.
        """
        return {TEAM_STATE_KEY: self.to_state_dict()}
