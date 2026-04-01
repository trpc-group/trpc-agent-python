# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Team Message Builder for constructing agent messages from state.

This module provides the TeamMessageBuilder class that builds message context
for team leader and member execution from TeamRunContext state.

Key design:
1. Leader messages are built from TeamRunContext.leader_history (text-only)
2. Member messages are isolated with optional interaction sharing
3. All messages are pure text - no raw function_call/function_response Parts
4. History can be limited to the last N runs (invocations)
"""

from __future__ import annotations

from typing import List

from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from ._team_run_context import TeamRunContext

# Used in multi-turn to force leader_agent think about whether the task is finished.
# This prompt is needed because in multi-turn, because the leader_agent may being confuse about
#   what to do next in our tests(by using deepseek-v3).
_LEADER_MULTI_TURN_TRANSITION_PROMPT = """
Please think about whether the user's task is finished.
- If finished, respond properly.
- If not, continue execution.
"""


class TeamMessageBuilder:
    """Builds message context for team leader and member execution from state.

    Key principles:
    1. Leader messages are built from TeamRunContext.leader_history (text-only)
    2. Members get isolated context with optional history sharing
    3. All content is converted to text (no raw tool call/response Parts)
    4. History can be limited to the last N runs via num_*_history_runs options

    Attributes:
        share_team_history: Whether to share team-level conversation history with members.
        num_team_history_runs: Number of past team runs to share with members.
        share_member_interactions: Whether to share current run's member interactions.
        num_member_history_runs: Number of past runs to include for member self history.
        add_history_to_leader: Whether to include conversation history for leader.
        num_leader_history_runs: Number of past runs to include for leader.
    """

    def __init__(
        self,
        share_team_history: bool = False,
        num_team_history_runs: int = 3,
        share_member_interactions: bool = False,
        num_member_history_runs: int = 0,
        add_history_to_leader: bool = True,
        num_leader_history_runs: int = 3,
    ):
        """Initialize the TeamMessageBuilder.

        Args:
            share_team_history: Whether to share team-level history with members.
            num_team_history_runs: Number of past runs (invocations) to include in member history.
            share_member_interactions: Whether to share interactions between members.
            num_member_history_runs: Number of past runs (invocations) to include for member self history.
            add_history_to_leader: Whether to include past invocations for leader.
            num_leader_history_runs: Number of past runs (invocations) to include for leader.
        """
        self.share_team_history = share_team_history
        self.num_team_history_runs = num_team_history_runs
        self.share_member_interactions = share_member_interactions
        self.num_member_history_runs = num_member_history_runs
        self.add_history_to_leader = add_history_to_leader
        self.num_leader_history_runs = num_leader_history_runs

    def build_member_messages(
        self,
        task: str,
        team_run_context: TeamRunContext,
        member_name: str = "",
    ) -> List[Content]:
        """Build messages for a member agent.

        Text format pattern:
        1. Member interactions from other members (if enabled) - <member_interaction_context>
        2. Member self history (if enabled) - <member_self_history_context>
        3. Team history (if enabled) - <team_history_context>
        4. Current task

        All are combined into a single user message string.

        Args:
            task: The task to assign to the member.
            team_run_context: Runtime context with recorded interactions.
            member_name: Name of the member being delegated to.

        Returns:
            List of Content objects to use as member's conversation history.
        """
        task_parts: List[str] = []

        # 1. Add member interactions (if enabled) - only current run
        if self.share_member_interactions:
            enable_member_history = bool(member_name and self.num_member_history_runs > 0)
            exclude_member_name = member_name if enable_member_history else ""
            interactions_str = self._get_member_interactions(
                team_run_context=team_run_context,
                exclude_member_name=exclude_member_name,
            )
            if interactions_str:
                task_parts.append(interactions_str)

        # 2. Add member self history (if enabled) - limited by num_member_history_runs
        if member_name and self.num_member_history_runs > 0:
            member_history_str = self._get_member_self_history(
                team_run_context=team_run_context,
                member_name=member_name,
                num_runs=self.num_member_history_runs,
            )
            if member_history_str:
                task_parts.append(member_history_str)

        # 3. Add team history (if enabled) - limited by num_team_history_runs
        if self.share_team_history:
            history_str = self._get_team_history(
                team_run_context=team_run_context,
                num_runs=self.num_team_history_runs,
            )
            if history_str:
                task_parts.append(history_str)

        # 4. Add the current task
        task_parts.append(task)

        # Combine into single user message
        combined_task = "\n\n".join(task_parts)
        task_message = Content(role="user", parts=[Part.from_text(text=combined_task)])

        return [task_message]

    def build_leader_messages(
        self,
        team_run_context: TeamRunContext,
    ) -> List[Content]:
        """Build messages for the leader agent from TeamRunContext state.

        Returns a single user message containing:
        1. Team history (if add_history_to_leader=True)
        2. Current user_content

        Combined format: "<team_history>\n<user_content>"

        Args:
            team_run_context: Runtime context containing leader_history.
            user_content: The current user message text.

        Returns:
            List with a single Content object for leader's input.
        """
        message_parts: List[str] = []

        # 1. Add team history (if enabled)
        if self.add_history_to_leader:
            history_str = self._get_team_history(
                team_run_context=team_run_context,
                num_runs=self.num_leader_history_runs,
            )
            if history_str:
                message_parts.append(history_str)

        # Combine into single user message
        if not message_parts:
            return []

        message_parts.append(_LEADER_MULTI_TURN_TRANSITION_PROMPT)

        combined_message = "\n\n".join(message_parts)
        return [Content(role="user", parts=[Part.from_text(text=combined_message)])]

    def _get_team_history(self, team_run_context: TeamRunContext, num_runs: int) -> str:
        """Get team history from TeamRunContext.

        History is limited by the provided num_runs value.

        Returns formatted string:
        <team_history_context>
        [Previous interactions and responses]
        </team_history_context>

        Args:
            team_run_context: Runtime context with leader_history.
            num_runs: Number of past invocations to include.

        Returns:
            Formatted team history string, or empty string if no history.
        """
        # Get history limited by the specified run count
        limited_history = team_run_context.get_leader_history_for_runs(num_runs)

        if not limited_history:
            return ""

        # Format leader history as team context
        history_parts = ["<team_history_context>"]

        for entry in limited_history:
            role = entry.get('role', 'user')
            text = entry.get('text', '')
            if text.strip():
                if role == 'user':
                    history_parts.append(f"User: {text}")
                else:
                    history_parts.append(f"Assistant: {text}")

        history_parts.append("</team_history_context>")
        return "\n".join(history_parts)

    def _get_member_interactions(
        self,
        team_run_context: TeamRunContext,
        exclude_member_name: str = "",
    ) -> str:
        """Get interactions from current team run.

        Only includes interactions from the current invocation, not past runs.

        Returns formatted string:
        <member_interaction_context>
        See below interactions wit other team members.
        Member: <name>
        Task: <task>
        Response: <response>

        </member_interaction_context>

        Args:
            team_run_context: Runtime context containing recorded interactions.
            exclude_member_name: Member name to exclude from interactions.

        Returns:
            Formatted member interactions string, or empty string if no interactions.
        """
        # Get only current run's interactions
        current_interactions = team_run_context.get_current_run_interactions()
        if exclude_member_name:
            current_interactions = [
                interaction for interaction in current_interactions if interaction.get('member') != exclude_member_name
            ]

        if not current_interactions:
            return ""

        parts = ["<member_interaction_context>"]
        parts.append("See below interactions with other team members.")

        for interaction in current_interactions:
            parts.append(f"Member: {interaction['member']}")
            parts.append(f"Task: {interaction['task']}")
            parts.append(f"Response: {interaction['response']}")
            parts.append("")  # Empty line

        parts.append("</member_interaction_context>")
        return "\n".join(parts)

    def _get_member_self_history(
        self,
        team_run_context: TeamRunContext,
        member_name: str,
        num_runs: int,
    ) -> str:
        """Get member self history from TeamRunContext.

        Returns formatted string:
        <member_self_history_context>
        See below your previous interactions in this team.
        Task: <task>
        Response: <response>
        </member_self_history_context>

        Args:
            team_run_context: Runtime context containing recorded interactions.
            member_name: Name of the member to get self history for.
            num_runs: Number of past invocations to include.

        Returns:
            Formatted member self history string, or empty string if no history.
        """
        member_history = team_run_context.get_member_interactions_for_runs(
            member_name=member_name,
            num_runs=num_runs,
        )
        if not member_history:
            return ""

        parts = ["<member_self_history_context>"]
        parts.append("See below your previous interactions in this team.")

        for interaction in member_history:
            parts.append(f"Task: {interaction['task']}")
            parts.append(f"Response: {interaction['response']}")
            parts.append("")

        parts.append("</member_self_history_context>")
        return "\n".join(parts)
