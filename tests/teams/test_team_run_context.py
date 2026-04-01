# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for TeamRunContext."""

from trpc_agent_sdk.teams.core import TEAM_STATE_KEY
from trpc_agent_sdk.teams.core import TeamRunContext


class TestTeamRunContextBasic:
    """Tests for TeamRunContext basic functionality."""

    def test_init_default_values(self):
        """Test TeamRunContext initializes with empty default values."""
        ctx = TeamRunContext()
        assert ctx.interactions == []
        assert ctx.team_name == ""
        assert ctx.leader_history == []
        assert ctx.current_invocation_id == ""
        assert ctx.pending_function_call_id == ""

    def test_init_with_team_name(self):
        """Test TeamRunContext initializes with team name."""
        ctx = TeamRunContext(team_name="test_team")
        assert ctx.team_name == "test_team"


class TestTeamRunContextInteractions:
    """Tests for interaction recording functionality."""

    def test_add_interaction(self):
        """Test adding a single member interaction."""
        ctx = TeamRunContext(current_invocation_id="inv-123")
        ctx.add_interaction("researcher", "Find information", "Found results")

        assert len(ctx.interactions) == 1
        assert ctx.interactions[0]["member"] == "researcher"
        assert ctx.interactions[0]["task"] == "Find information"
        assert ctx.interactions[0]["response"] == "Found results"
        assert ctx.interactions[0]["invocation_id"] == "inv-123"

    def test_add_multiple_interactions(self):
        """Test adding multiple member interactions."""
        ctx = TeamRunContext(current_invocation_id="inv-123")
        ctx.add_interaction("researcher", "Task 1", "Response 1")
        ctx.add_interaction("writer", "Task 2", "Response 2")

        assert len(ctx.interactions) == 2
        assert ctx.interactions[0]["member"] == "researcher"
        assert ctx.interactions[1]["member"] == "writer"

    def test_get_current_run_interactions(self):
        """Test filtering interactions by current invocation ID."""
        ctx = TeamRunContext()

        # Add interactions from different invocations
        ctx.current_invocation_id = "inv-1"
        ctx.add_interaction("researcher", "Task 1", "Response 1")

        ctx.current_invocation_id = "inv-2"
        ctx.add_interaction("writer", "Task 2", "Response 2")
        ctx.add_interaction("editor", "Task 3", "Response 3")

        # Should only return interactions from current invocation (inv-2)
        current = ctx.get_current_run_interactions()
        assert len(current) == 2
        assert all(i["invocation_id"] == "inv-2" for i in current)

    def test_get_current_run_interactions_empty_invocation_id(self):
        """Test get_current_run_interactions returns all when no invocation_id set."""
        ctx = TeamRunContext()
        ctx.add_interaction("researcher", "Task 1", "Response 1")
        ctx.add_interaction("writer", "Task 2", "Response 2")

        # Should return all interactions when current_invocation_id is empty
        current = ctx.get_current_run_interactions()
        assert len(current) == 2

    def test_get_member_interactions_for_runs_filters_member_and_runs(self):
        """Test member interactions are filtered by member name and run count."""
        ctx = TeamRunContext()

        ctx.current_invocation_id = "inv-1"
        ctx.add_interaction("researcher", "Old task", "Old response")
        ctx.add_interaction("writer", "Old writer task", "Old writer response")

        ctx.current_invocation_id = "inv-2"
        ctx.add_interaction("researcher", "New task", "New response")
        ctx.add_interaction("writer", "New writer task", "New writer response")

        ctx.current_invocation_id = "inv-3"
        ctx.add_interaction("researcher", "Latest task", "Latest response")

        interactions = ctx.get_member_interactions_for_runs("researcher", 2)

        assert len(interactions) == 2
        assert all(item["member"] == "researcher" for item in interactions)
        assert all(item["invocation_id"] in {"inv-2", "inv-3"} for item in interactions)
        assert all(item["task"] != "Old task" for item in interactions)

    def test_get_member_interactions_for_runs_zero_returns_empty(self):
        """Test member interactions returns empty when num_runs <= 0."""
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_interaction("researcher", "Task", "Response")

        assert ctx.get_member_interactions_for_runs("researcher", 0) == []
        assert ctx.get_member_interactions_for_runs("researcher", -1) == []

    def test_get_member_interactions_for_runs_legacy_entries(self):
        """Test member interactions fallback works for legacy entries without invocation_id."""
        ctx = TeamRunContext()
        ctx.add_interaction("researcher", "Task 1", "Response 1")
        ctx.add_interaction("writer", "Task 2", "Response 2")

        interactions = ctx.get_member_interactions_for_runs("researcher", 1)
        assert len(interactions) == 1
        assert interactions[0]["member"] == "researcher"


class TestTeamRunContextLeaderHistory:
    """Tests for leader history management."""

    def test_add_leader_message_user(self):
        """Test adding user message to leader history."""
        ctx = TeamRunContext(current_invocation_id="inv-123")
        ctx.add_leader_message("user", "Hello, please help me")

        assert len(ctx.leader_history) == 1
        assert ctx.leader_history[0]["role"] == "user"
        assert ctx.leader_history[0]["text"] == "Hello, please help me"
        assert ctx.leader_history[0]["invocation_id"] == "inv-123"

    def test_add_leader_message_model(self):
        """Test adding model message to leader history."""
        ctx = TeamRunContext(current_invocation_id="inv-123")
        ctx.add_leader_message("model", "I will help you")

        assert len(ctx.leader_history) == 1
        assert ctx.leader_history[0]["role"] == "model"

    def test_add_leader_message_empty_text_ignored(self):
        """Test that empty messages are not added to history."""
        ctx = TeamRunContext()
        ctx.add_leader_message("user", "")
        ctx.add_leader_message("user", "   ")  # whitespace only

        assert len(ctx.leader_history) == 0

    def test_add_delegation_record(self):
        """Test adding delegation record to leader history."""
        ctx = TeamRunContext(current_invocation_id="inv-123")
        ctx.add_delegation_record("researcher", "Find data", "Found data")

        assert len(ctx.leader_history) == 1
        record = ctx.leader_history[0]
        assert record["role"] == "model"
        assert "researcher" in record["text"]
        assert "Find data" in record["text"]
        assert "Found data" in record["text"]
        assert "<member_interaction_context>" in record["text"]
        assert "</member_interaction_context>" in record["text"]

    def test_get_leader_history_for_runs(self):
        """Test filtering leader history by number of runs."""
        ctx = TeamRunContext()

        # Add history from invocation 1
        ctx.current_invocation_id = "inv-1"
        ctx.add_leader_message("user", "Message 1")
        ctx.add_leader_message("model", "Response 1")

        # Add history from invocation 2
        ctx.current_invocation_id = "inv-2"
        ctx.add_leader_message("user", "Message 2")
        ctx.add_leader_message("model", "Response 2")

        # Add history from invocation 3
        ctx.current_invocation_id = "inv-3"
        ctx.add_leader_message("user", "Message 3")
        ctx.add_leader_message("model", "Response 3")

        # Get last 2 runs
        history = ctx.get_leader_history_for_runs(2)
        invocation_ids = set(h["invocation_id"] for h in history)
        assert invocation_ids == {"inv-2", "inv-3"}

    def test_get_leader_history_for_runs_zero(self):
        """Test getting history with num_runs=0 returns empty list."""
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_leader_message("user", "Message")

        history = ctx.get_leader_history_for_runs(0)
        assert history == []

    def test_get_leader_history_for_runs_negative(self):
        """Test getting history with negative num_runs returns empty list."""
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_leader_message("user", "Message")

        history = ctx.get_leader_history_for_runs(-1)
        assert history == []

    def test_get_leader_history_for_runs_all(self):
        """Test getting more runs than available returns all history."""
        ctx = TeamRunContext(current_invocation_id="inv-1")
        ctx.add_leader_message("user", "Message 1")
        ctx.add_leader_message("model", "Response 1")

        history = ctx.get_leader_history_for_runs(10)  # More than available
        assert len(history) == 2


class TestTeamRunContextHITL:
    """Tests for Human-in-the-loop (HITL) functionality."""

    def test_set_pending_hitl(self):
        """Test setting pending HITL state."""
        ctx = TeamRunContext()
        ctx.set_pending_hitl("func-call-123")

        assert ctx.pending_function_call_id == "func-call-123"
        assert ctx.has_pending_hitl() is True

    def test_clear_pending_hitl(self):
        """Test clearing pending HITL state."""
        ctx = TeamRunContext(pending_function_call_id="func-call-123")
        ctx.clear_pending_hitl()

        assert ctx.pending_function_call_id == ""
        assert ctx.has_pending_hitl() is False

    def test_has_pending_hitl_false(self):
        """Test has_pending_hitl returns False when no pending state."""
        ctx = TeamRunContext()
        assert ctx.has_pending_hitl() is False

    def test_has_pending_hitl_true(self):
        """Test has_pending_hitl returns True when pending state exists."""
        ctx = TeamRunContext(pending_function_call_id="func-123")
        assert ctx.has_pending_hitl() is True


class TestTeamRunContextStateSerialization:
    """Tests for state serialization and deserialization."""

    def test_to_state_dict(self):
        """Test serializing TeamRunContext to state dictionary."""
        ctx = TeamRunContext(
            team_name="test_team",
            current_invocation_id="inv-123",
            pending_function_call_id="func-456",
        )
        ctx.add_leader_message("user", "Hello")
        ctx.add_interaction("researcher", "Task", "Response")

        state_dict = ctx.to_state_dict()

        assert state_dict["team_name"] == "test_team"
        assert state_dict["current_invocation_id"] == "inv-123"
        assert state_dict["pending_function_call_id"] == "func-456"
        assert len(state_dict["leader_history"]) == 1
        assert len(state_dict["interactions"]) == 1

    def test_from_state_empty(self):
        """Test restoring TeamRunContext from empty state."""
        ctx = TeamRunContext.from_state({}, team_name="test_team")

        assert ctx.team_name == "test_team"
        assert ctx.interactions == []
        assert ctx.leader_history == []

    def test_from_state_with_data(self):
        """Test restoring TeamRunContext from state with data."""
        state = {
            TEAM_STATE_KEY: {
                "team_name": "test_team",
                "interactions": [{
                    "member": "researcher",
                    "task": "Task",
                    "response": "Response",
                    "invocation_id": "inv-1"
                }],
                "leader_history": [{
                    "role": "user",
                    "text": "Hello",
                    "invocation_id": "inv-1"
                }],
                "current_invocation_id": "inv-1",
                "pending_function_call_id": "func-123",
            }
        }

        ctx = TeamRunContext.from_state(state)

        assert ctx.team_name == "test_team"
        assert len(ctx.interactions) == 1
        assert ctx.interactions[0]["member"] == "researcher"
        assert len(ctx.leader_history) == 1
        assert ctx.pending_function_call_id == "func-123"

    def test_get_state_delta(self):
        """Test getting state delta for session update."""
        ctx = TeamRunContext(team_name="test_team")
        ctx.add_leader_message("user", "Hello")

        delta = ctx.get_state_delta()

        assert TEAM_STATE_KEY in delta
        assert delta[TEAM_STATE_KEY]["team_name"] == "test_team"
        assert len(delta[TEAM_STATE_KEY]["leader_history"]) == 1

    def test_roundtrip_serialization(self):
        """Test full roundtrip: create -> serialize -> deserialize."""
        original = TeamRunContext(
            team_name="test_team",
            current_invocation_id="inv-123",
        )
        original.add_leader_message("user", "Hello")
        original.add_leader_message("model", "Hi there")
        original.add_interaction("researcher", "Find info", "Found it")
        original.add_delegation_record("writer", "Write article", "Article written")
        original.set_pending_hitl("func-456")

        # Serialize
        state_dict = original.to_state_dict()
        state = {TEAM_STATE_KEY: state_dict}

        # Deserialize
        restored = TeamRunContext.from_state(state)

        # Verify
        assert restored.team_name == original.team_name
        assert restored.current_invocation_id == original.current_invocation_id
        assert restored.pending_function_call_id == original.pending_function_call_id
        assert len(restored.leader_history) == len(original.leader_history)
        assert len(restored.interactions) == len(original.interactions)

        # Verify content
        for i, h in enumerate(original.leader_history):
            assert restored.leader_history[i]["role"] == h["role"]
            assert restored.leader_history[i]["text"] == h["text"]


class TestTeamRunContextClear:
    """Tests for clearing context."""

    def test_clear(self):
        """Test clearing all context data."""
        ctx = TeamRunContext(
            team_name="test_team",
            current_invocation_id="inv-123",
        )
        ctx.add_leader_message("user", "Hello")
        ctx.add_interaction("researcher", "Task", "Response")
        ctx.set_pending_hitl("func-123")

        ctx.clear()

        assert ctx.interactions == []
        assert ctx.leader_history == []
        assert ctx.pending_function_call_id == ""
        # team_name and current_invocation_id should remain
        assert ctx.team_name == "test_team"
        assert ctx.current_invocation_id == "inv-123"
