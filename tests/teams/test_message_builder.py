# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for TeamMessageBuilder."""

import pytest
from trpc_agent_sdk.teams.core import TeamMessageBuilder
from trpc_agent_sdk.teams.core import TeamRunContext


class TestTeamMessageBuilderInit:
    """Tests for TeamMessageBuilder initialization."""

    def test_default_values(self):
        """Test default initialization values."""
        builder = TeamMessageBuilder()
        assert builder.share_team_history is False
        assert builder.num_team_history_runs == 3
        assert builder.share_member_interactions is False
        assert builder.num_member_history_runs == 0
        assert builder.add_history_to_leader is True
        assert builder.num_leader_history_runs == 3

    def test_custom_values(self):
        """Test initialization with custom values."""
        builder = TeamMessageBuilder(
            share_team_history=True,
            num_team_history_runs=5,
            share_member_interactions=True,
            num_member_history_runs=2,
            add_history_to_leader=False,
            num_leader_history_runs=10,
        )
        assert builder.share_team_history is True
        assert builder.num_team_history_runs == 5
        assert builder.share_member_interactions is True
        assert builder.num_member_history_runs == 2
        assert builder.add_history_to_leader is False
        assert builder.num_leader_history_runs == 10


class TestBuildMemberMessages:
    """Tests for building member agent messages."""

    def test_build_member_messages_basic(self):
        """Test building basic member messages with just a task."""
        builder = TeamMessageBuilder()
        ctx = TeamRunContext()
        task = "Research the topic of AI"

        messages = builder.build_member_messages(task=task, team_run_context=ctx)

        assert len(messages) == 1
        assert messages[0].role == "user"
        assert len(messages[0].parts) == 1
        assert messages[0].parts[0].text == task

    def test_build_member_messages_with_team_history(self):
        """Test building member messages with team history sharing enabled."""
        builder = TeamMessageBuilder(share_team_history=True)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_leader_message("user", "Initial question")
        ctx.add_leader_message("model", "Initial response")
        task = "Do something"

        messages = builder.build_member_messages(task=task, team_run_context=ctx)

        assert len(messages) == 1
        content = messages[0].parts[0].text
        assert "<team_history_context>" in content
        assert "Initial question" in content
        assert "Initial response" in content
        assert task in content

    def test_build_member_messages_with_member_interactions(self):
        """Test building member messages with member interaction sharing."""
        builder = TeamMessageBuilder(share_member_interactions=True)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_interaction("researcher", "Find data", "Found data about AI")
        task = "Write summary"

        messages = builder.build_member_messages(task=task, team_run_context=ctx)

        assert len(messages) == 1
        content = messages[0].parts[0].text
        assert "<member_interaction_context>" in content
        assert "researcher" in content
        assert "Found data about AI" in content
        assert task in content

    def test_build_member_messages_member_self_history_disabled_by_default(self):
        """Test member self history is disabled when num_member_history_runs=0."""
        builder = TeamMessageBuilder(num_member_history_runs=0)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_interaction("researcher", "Find data", "Found data")

        messages = builder.build_member_messages(
            task="Write summary",
            team_run_context=ctx,
            member_name="researcher",
        )

        content = messages[0].parts[0].text
        assert "<member_self_history_context>" not in content
        assert "Write summary" in content

    def test_build_member_messages_with_member_self_history_current_run(self):
        """Test member self history includes same-member records from current run."""
        builder = TeamMessageBuilder(num_member_history_runs=1)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_interaction("researcher", "First task", "First response")
        ctx.add_interaction("writer", "Writer task", "Writer response")

        messages = builder.build_member_messages(
            task="Second task",
            team_run_context=ctx,
            member_name="researcher",
        )

        content = messages[0].parts[0].text
        assert "<member_self_history_context>" in content
        assert "Task: First task" in content
        assert "Response: First response" in content
        assert "Task: Writer task" not in content

    def test_build_member_messages_member_self_history_limited_by_runs(self):
        """Test member self history respects num_member_history_runs."""
        builder = TeamMessageBuilder(num_member_history_runs=1)
        ctx = TeamRunContext()

        ctx.current_invocation_id = "inv-1"
        ctx.add_interaction("researcher", "Old task", "Old response")

        ctx.current_invocation_id = "inv-2"
        ctx.add_interaction("researcher", "New task", "New response")

        messages = builder.build_member_messages(
            task="Current task",
            team_run_context=ctx,
            member_name="researcher",
        )

        content = messages[0].parts[0].text
        assert "Task: Old task" not in content
        assert "Task: New task" in content

    def test_build_member_messages_self_history_avoids_duplicates_in_shared_interactions(self):
        """Test shared interactions exclude self entries when self history is enabled."""
        builder = TeamMessageBuilder(
            share_member_interactions=True,
            num_member_history_runs=1,
        )
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_interaction("researcher", "Self task", "Self response")
        ctx.add_interaction("writer", "Peer task", "Peer response")

        messages = builder.build_member_messages(
            task="Current task",
            team_run_context=ctx,
            member_name="researcher",
        )

        content = messages[0].parts[0].text
        assert "<member_interaction_context>" in content
        assert "<member_self_history_context>" in content
        assert "Member: writer" in content
        assert "Member: researcher" not in content
        assert "Task: Self task" in content
        assert "Response: Self response" in content

    def test_build_member_messages_no_history_when_disabled(self):
        """Test that history is not included when sharing is disabled."""
        builder = TeamMessageBuilder(
            share_team_history=False,
            share_member_interactions=False,
        )
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_leader_message("user", "Should not appear")
        ctx.add_interaction("researcher", "Task", "Response")
        task = "Do task"

        messages = builder.build_member_messages(task=task, team_run_context=ctx)

        content = messages[0].parts[0].text
        assert "<team_history_context>" not in content
        assert "<member_interaction_context>" not in content
        assert content == task


class TestBuildLeaderMessages:
    """Tests for building team leader messages."""

    def test_build_leader_messages_empty_history(self):
        """Test building leader messages with no history."""
        builder = TeamMessageBuilder(add_history_to_leader=True)
        ctx = TeamRunContext()

        messages = builder.build_leader_messages(team_run_context=ctx)

        # Should return empty list when no history
        assert messages == []

    def test_build_leader_messages_with_history(self):
        """Test building leader messages with conversation history."""
        builder = TeamMessageBuilder(add_history_to_leader=True)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_leader_message("user", "What is AI?")
        ctx.add_leader_message("model", "AI is artificial intelligence")

        messages = builder.build_leader_messages(team_run_context=ctx)

        assert len(messages) == 1
        content = messages[0].parts[0].text
        assert "<team_history_context>" in content
        assert "What is AI?" in content
        assert "AI is artificial intelligence" in content

    def test_build_leader_messages_history_disabled(self):
        """Test that leader history is not included when disabled."""
        builder = TeamMessageBuilder(add_history_to_leader=False)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_leader_message("user", "Should not appear")

        messages = builder.build_leader_messages(team_run_context=ctx)

        # Should return empty list when history disabled
        assert messages == []

    def test_build_leader_messages_includes_transition_prompt(self):
        """Test that leader messages include the transition prompt."""
        builder = TeamMessageBuilder(add_history_to_leader=True)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_leader_message("user", "Question")

        messages = builder.build_leader_messages(team_run_context=ctx)

        content = messages[0].parts[0].text
        # Check for the transition prompt content
        assert "think about whether" in content.lower() or "finished" in content.lower()


class TestTeamHistoryFormatting:
    """Tests for team history formatting."""

    def test_team_history_formatting_user_messages(self):
        """Test that user messages are formatted correctly in history."""
        builder = TeamMessageBuilder(share_team_history=True)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_leader_message("user", "User question here")

        messages = builder.build_member_messages(task="Task", team_run_context=ctx)

        content = messages[0].parts[0].text
        assert "User: User question here" in content

    def test_team_history_formatting_model_messages(self):
        """Test that model messages are formatted correctly in history."""
        builder = TeamMessageBuilder(share_team_history=True)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_leader_message("model", "Model response here")

        messages = builder.build_member_messages(task="Task", team_run_context=ctx)

        content = messages[0].parts[0].text
        assert "Assistant: Model response here" in content

    def test_team_history_xml_tags(self):
        """Test that team history is wrapped in XML tags."""
        builder = TeamMessageBuilder(share_team_history=True)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_leader_message("user", "Question")

        messages = builder.build_member_messages(task="Task", team_run_context=ctx)

        content = messages[0].parts[0].text
        assert content.count("<team_history_context>") == 1
        assert content.count("</team_history_context>") == 1


class TestMemberInteractionsFormatting:
    """Tests for member interactions formatting."""

    def test_member_interactions_formatting(self):
        """Test that member interactions are formatted correctly."""
        builder = TeamMessageBuilder(share_member_interactions=True)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_interaction("researcher", "Find info", "Found info")
        ctx.add_interaction("writer", "Write doc", "Doc written")

        messages = builder.build_member_messages(task="Task", team_run_context=ctx)

        content = messages[0].parts[0].text
        assert "Member: researcher" in content
        assert "Task: Find info" in content
        assert "Response: Found info" in content
        assert "Member: writer" in content

    def test_member_interactions_xml_tags(self):
        """Test that member interactions are wrapped in XML tags."""
        builder = TeamMessageBuilder(share_member_interactions=True)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_interaction("researcher", "Task", "Response")

        messages = builder.build_member_messages(task="Task", team_run_context=ctx)

        content = messages[0].parts[0].text
        assert "<member_interaction_context>" in content
        assert "</member_interaction_context>" in content

    def test_member_interactions_only_current_run(self):
        """Test that only current run interactions are included."""
        builder = TeamMessageBuilder(share_member_interactions=True)
        ctx = TeamRunContext()

        # Add interaction from previous invocation
        ctx.current_invocation_id = "inv-1"
        ctx.add_interaction("old_member", "Old task", "Old response")

        # Add interaction from current invocation
        ctx.current_invocation_id = "inv-2"
        ctx.add_interaction("new_member", "New task", "New response")

        messages = builder.build_member_messages(task="Task", team_run_context=ctx)

        content = messages[0].parts[0].text
        assert "new_member" in content
        assert "old_member" not in content


class TestHistoryLimiting:
    """Tests for history run limiting."""

    def test_leader_history_limited_by_runs(self):
        """Test that leader history is limited by num_leader_history_runs."""
        builder = TeamMessageBuilder(
            add_history_to_leader=True,
            num_team_history_runs=1,  # Member history limit
            num_leader_history_runs=2,  # Leader history limit
        )
        ctx = TeamRunContext()

        # Add history from 3 different invocations
        for i in range(1, 4):
            ctx.current_invocation_id = f"inv-{i}"
            ctx.add_leader_message("user", f"Question {i}")
            ctx.add_leader_message("model", f"Answer {i}")

        messages = builder.build_leader_messages(team_run_context=ctx)

        content = messages[0].parts[0].text
        # Should only include last 2 runs (inv-2 and inv-3)
        assert "Question 1" not in content
        assert "Question 2" in content
        assert "Question 3" in content

    def test_team_history_limited_by_runs(self):
        """Test that team history for members is limited by num_team_history_runs."""
        builder = TeamMessageBuilder(
            share_team_history=True,
            num_team_history_runs=1,
        )
        ctx = TeamRunContext()

        # Add history from 2 different invocations
        ctx.current_invocation_id = "inv-1"
        ctx.add_leader_message("user", "Old question")

        ctx.current_invocation_id = "inv-2"
        ctx.add_leader_message("user", "New question")

        messages = builder.build_member_messages(task="Task", team_run_context=ctx)

        content = messages[0].parts[0].text
        # Should only include last 1 run
        assert "Old question" not in content
        assert "New question" in content


class TestMemberInteractionsExclusion:
    """Tests for member interaction exclusion logic."""

    def test_exclude_member_removes_self_from_interactions(self):
        """Test that excluding a member removes only their entries when self-history is enabled."""
        builder = TeamMessageBuilder(
            share_member_interactions=True,
            num_member_history_runs=1,
        )
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_interaction("researcher", "Task A", "Response A")
        ctx.add_interaction("writer", "Task B", "Response B")

        messages = builder.build_member_messages(
            task="Task",
            team_run_context=ctx,
            member_name="researcher",
        )

        content = messages[0].parts[0].text
        assert "Member: writer" in content
        assert "Member: researcher" not in content

    def test_exclude_member_all_interactions_filtered(self):
        """Test when all current-run interactions belong to excluded member."""
        builder = TeamMessageBuilder(
            share_member_interactions=True,
            num_member_history_runs=1,
        )
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_interaction("researcher", "Task A", "Response A")

        messages = builder.build_member_messages(
            task="Do work",
            team_run_context=ctx,
            member_name="researcher",
        )

        content = messages[0].parts[0].text
        assert "<member_interaction_context>" not in content
        assert "Do work" in content

    def test_no_member_interactions_returns_empty(self):
        """Test member interactions when there are no interactions."""
        builder = TeamMessageBuilder(share_member_interactions=True)
        ctx = TeamRunContext(current_invocation_id="inv-1")

        messages = builder.build_member_messages(task="Task", team_run_context=ctx)

        content = messages[0].parts[0].text
        assert "<member_interaction_context>" not in content


class TestMemberSelfHistoryDirect:
    """Tests for member self history edge cases."""

    def test_member_self_history_no_history(self):
        """Test member self history when member has no history."""
        builder = TeamMessageBuilder(num_member_history_runs=3)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_interaction("writer", "Task", "Response")

        messages = builder.build_member_messages(
            task="Do work",
            team_run_context=ctx,
            member_name="researcher",
        )

        content = messages[0].parts[0].text
        assert "<member_self_history_context>" not in content

    def test_member_self_history_without_member_name(self):
        """Test member self history is not added when no member_name."""
        builder = TeamMessageBuilder(num_member_history_runs=3)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_interaction("researcher", "Task", "Response")

        messages = builder.build_member_messages(
            task="Do work",
            team_run_context=ctx,
        )

        content = messages[0].parts[0].text
        assert "<member_self_history_context>" not in content


class TestTeamHistoryWhitespace:
    """Tests for team history with edge-case text content."""

    def test_whitespace_only_entries_skipped(self):
        """Test that whitespace-only history entries are skipped."""
        builder = TeamMessageBuilder(share_team_history=True)
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.leader_history = [
            {"role": "user", "text": "   ", "invocation_id": "inv-1"},
            {"role": "model", "text": "Real response", "invocation_id": "inv-1"},
        ]

        messages = builder.build_member_messages(task="Task", team_run_context=ctx)

        content = messages[0].parts[0].text
        assert "Real response" in content


class TestMessageCombination:
    """Tests for combining different message parts."""

    def test_all_parts_combined(self):
        """Test that all enabled parts are combined in correct order."""
        builder = TeamMessageBuilder(
            share_team_history=True,
            share_member_interactions=True,
        )
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_leader_message("user", "History message")
        ctx.add_interaction("member", "Task done", "Response here")
        task = "Final task"

        messages = builder.build_member_messages(task=task, team_run_context=ctx)

        content = messages[0].parts[0].text
        # Check order: interactions first, then history, then task
        interaction_pos = content.find("<member_interaction_context>")
        history_pos = content.find("<team_history_context>")
        task_pos = content.find(task)

        assert interaction_pos < history_pos < task_pos

    def test_parts_separated_by_newlines(self):
        """Test that parts are properly separated."""
        builder = TeamMessageBuilder(
            share_team_history=True,
            share_member_interactions=True,
        )
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_leader_message("user", "History")
        ctx.add_interaction("member", "Task", "Response")

        messages = builder.build_member_messages(task="Task", team_run_context=ctx)

        content = messages[0].parts[0].text
        # Should have double newlines between sections
        assert "\n\n" in content
