# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for InMemoryEvalSetsManager."""

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import EvalCase
from trpc_agent_sdk.evaluation import EvalSet
from trpc_agent_sdk.evaluation import InMemoryEvalSetsManager
from trpc_agent_sdk.evaluation import SessionInput


def _make_case(eval_id="c1"):
    return EvalCase(
        eval_id=eval_id,
        conversation=[],
        session_input=SessionInput(app_name="a", user_id="u", state={}),
    )


class TestGetEvalSet:
    """Test suite for get_eval_set."""

    def test_returns_none_for_missing(self):
        """Test get_eval_set returns None for missing set."""
        m = InMemoryEvalSetsManager()
        assert m.get_eval_set("app1", "nonexistent") is None

    def test_returns_created_set(self):
        """Test get_eval_set returns a previously created set."""
        m = InMemoryEvalSetsManager()
        m.create_eval_set("app1", "set1")
        result = m.get_eval_set("app1", "set1")
        assert result is not None
        assert result.eval_set_id == "set1"


class TestCreateEvalSet:
    """Test suite for create_eval_set."""

    def test_creates_new_set(self):
        """Test create_eval_set creates a new set."""
        m = InMemoryEvalSetsManager()
        s = m.create_eval_set("app1", "set1")
        assert isinstance(s, EvalSet)
        assert s.eval_set_id == "set1"
        assert s.eval_cases == []

    def test_duplicate_raises(self):
        """Test create_eval_set raises on duplicate."""
        m = InMemoryEvalSetsManager()
        m.create_eval_set("app1", "set1")
        with pytest.raises(ValueError, match="already exists"):
            m.create_eval_set("app1", "set1")

    def test_different_apps_independent(self):
        """Test different apps have independent sets."""
        m = InMemoryEvalSetsManager()
        m.create_eval_set("app1", "set1")
        m.create_eval_set("app2", "set1")
        assert m.get_eval_set("app1", "set1") is not None
        assert m.get_eval_set("app2", "set1") is not None


class TestListEvalSets:
    """Test suite for list_eval_sets."""

    def test_empty_app(self):
        """Test list_eval_sets for unknown app returns empty."""
        m = InMemoryEvalSetsManager()
        assert m.list_eval_sets("unknown") == []

    def test_lists_created_sets(self):
        """Test list_eval_sets returns created set ids."""
        m = InMemoryEvalSetsManager()
        m.create_eval_set("app1", "s1")
        m.create_eval_set("app1", "s2")
        result = m.list_eval_sets("app1")
        assert set(result) == {"s1", "s2"}


class TestGetEvalCase:
    """Test suite for get_eval_case."""

    def test_missing_app(self):
        """Test returns None for missing app."""
        m = InMemoryEvalSetsManager()
        assert m.get_eval_case("unknown", "s1", "c1") is None

    def test_missing_set(self):
        """Test returns None for missing set."""
        m = InMemoryEvalSetsManager()
        m.create_eval_set("app1", "s1")
        assert m.get_eval_case("app1", "s2", "c1") is None

    def test_missing_case(self):
        """Test returns None for missing case."""
        m = InMemoryEvalSetsManager()
        m.create_eval_set("app1", "s1")
        assert m.get_eval_case("app1", "s1", "nonexistent") is None

    def test_returns_added_case(self):
        """Test returns a previously added case."""
        m = InMemoryEvalSetsManager()
        m.create_eval_set("app1", "s1")
        c = _make_case("c1")
        m.add_eval_case("app1", "s1", c)
        assert m.get_eval_case("app1", "s1", "c1") is c


class TestAddEvalCase:
    """Test suite for add_eval_case."""

    def test_add_to_missing_set_raises(self):
        """Test add to missing set raises ValueError."""
        m = InMemoryEvalSetsManager()
        with pytest.raises(ValueError, match="not found"):
            m.add_eval_case("app1", "nonexistent", _make_case())

    def test_duplicate_case_raises(self):
        """Test adding duplicate case raises ValueError."""
        m = InMemoryEvalSetsManager()
        m.create_eval_set("app1", "s1")
        m.add_eval_case("app1", "s1", _make_case("c1"))
        with pytest.raises(ValueError, match="already exists"):
            m.add_eval_case("app1", "s1", _make_case("c1"))

    def test_updates_eval_set_list(self):
        """Test add_eval_case updates the EvalSet.eval_cases list."""
        m = InMemoryEvalSetsManager()
        m.create_eval_set("app1", "s1")
        m.add_eval_case("app1", "s1", _make_case("c1"))
        s = m.get_eval_set("app1", "s1")
        assert len(s.eval_cases) == 1
        assert s.eval_cases[0].eval_id == "c1"


class TestUpdateEvalCase:
    """Test suite for update_eval_case."""

    def test_update_missing_set_raises(self):
        """Test update on missing set raises ValueError."""
        m = InMemoryEvalSetsManager()
        with pytest.raises(ValueError, match="not found"):
            m.update_eval_case("app1", "nonexistent", _make_case())

    def test_update_missing_case_raises(self):
        """Test update on missing case raises ValueError."""
        m = InMemoryEvalSetsManager()
        m.create_eval_set("app1", "s1")
        with pytest.raises(ValueError, match="not found"):
            m.update_eval_case("app1", "s1", _make_case("nonexistent"))

    def test_update_replaces_case(self):
        """Test update replaces the case in both index and list."""
        m = InMemoryEvalSetsManager()
        m.create_eval_set("app1", "s1")
        c1 = _make_case("c1")
        m.add_eval_case("app1", "s1", c1)
        c1_updated = _make_case("c1")
        m.update_eval_case("app1", "s1", c1_updated)
        assert m.get_eval_case("app1", "s1", "c1") is c1_updated
        s = m.get_eval_set("app1", "s1")
        assert s.eval_cases[0] is c1_updated


class TestDeleteEvalCase:
    """Test suite for delete_eval_case."""

    def test_delete_missing_set_raises(self):
        """Test delete from missing set raises ValueError."""
        m = InMemoryEvalSetsManager()
        with pytest.raises(ValueError, match="not found"):
            m.delete_eval_case("app1", "nonexistent", "c1")

    def test_delete_missing_case_raises(self):
        """Test delete of missing case raises ValueError."""
        m = InMemoryEvalSetsManager()
        m.create_eval_set("app1", "s1")
        with pytest.raises(ValueError, match="not found"):
            m.delete_eval_case("app1", "s1", "nonexistent")

    def test_delete_removes_case(self):
        """Test delete removes case from both index and list."""
        m = InMemoryEvalSetsManager()
        m.create_eval_set("app1", "s1")
        m.add_eval_case("app1", "s1", _make_case("c1"))
        m.delete_eval_case("app1", "s1", "c1")
        assert m.get_eval_case("app1", "s1", "c1") is None
        s = m.get_eval_set("app1", "s1")
        assert len(s.eval_cases) == 0
