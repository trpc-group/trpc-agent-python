# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for eval sets manager utils (_eval_sets_manager_utils)."""

from unittest.mock import Mock

import pytest
from trpc_agent_sdk.evaluation._eval_case import EvalCase
from trpc_agent_sdk.evaluation._eval_case import SessionInput
from trpc_agent_sdk.evaluation._eval_set import EvalSet
from trpc_agent_sdk.evaluation._eval_sets_manager_utils import NotFoundError
from trpc_agent_sdk.evaluation._eval_sets_manager_utils import add_eval_case_to_eval_set
from trpc_agent_sdk.evaluation._eval_sets_manager_utils import delete_eval_case_from_eval_set
from trpc_agent_sdk.evaluation._eval_sets_manager_utils import get_eval_case_from_eval_set
from trpc_agent_sdk.evaluation._eval_sets_manager_utils import get_eval_set_from_app_and_id
from trpc_agent_sdk.evaluation._eval_sets_manager_utils import update_eval_case_in_eval_set


def _make_eval_case(eval_id: str) -> EvalCase:
    return EvalCase(
        eval_id=eval_id,
        conversation=[],
        session_input=SessionInput(app_name="a", user_id="u", state={}),
    )


class TestNotFoundError:
    """Test suite for NotFoundError."""

    def test_raise(self):
        """Test NotFoundError is an Exception."""
        with pytest.raises(NotFoundError):
            raise NotFoundError("not found")


class TestGetEvalSetFromAppAndId:
    """Test suite for get_eval_set_from_app_and_id."""

    def test_returns_eval_set_when_found(self):
        """Test returns EvalSet when manager returns it."""
        es = EvalSet(eval_set_id="set1", eval_cases=[])
        manager = Mock()
        manager.get_eval_set.return_value = es
        out = get_eval_set_from_app_and_id(manager, "app1", "set1")
        assert out == es

    def test_raises_when_not_found(self):
        """Test raises NotFoundError when manager returns None."""
        manager = Mock()
        manager.get_eval_set.return_value = None
        with pytest.raises(NotFoundError):
            get_eval_set_from_app_and_id(manager, "app1", "set1")


class TestGetEvalCaseFromEvalSet:
    """Test suite for get_eval_case_from_eval_set."""

    def test_returns_case_when_found(self):
        """Test returns EvalCase when eval_id matches."""
        c1 = _make_eval_case("case_001")
        c2 = _make_eval_case("case_002")
        eval_set = EvalSet(eval_set_id="set1", eval_cases=[c1, c2])
        out = get_eval_case_from_eval_set(eval_set, "case_002")
        assert out is c2

    def test_returns_none_when_not_found(self):
        """Test returns None when eval_id not in set."""
        eval_set = EvalSet(eval_set_id="set1", eval_cases=[_make_eval_case("case_001")])
        assert get_eval_case_from_eval_set(eval_set, "other") is None


class TestAddEvalCaseToEvalSet:
    """Test suite for add_eval_case_to_eval_set."""

    def test_adds_case(self):
        """Test add_eval_case adds case and returns same set."""
        eval_set = EvalSet(eval_set_id="set1", eval_cases=[_make_eval_case("c1")])
        new_case = _make_eval_case("c2")
        out = add_eval_case_to_eval_set(eval_set, new_case)
        assert out is eval_set
        assert len(eval_set.eval_cases) == 2
        assert eval_set.eval_cases[1].eval_id == "c2"

    def test_duplicate_eval_id_raises(self):
        """Test adding duplicate eval_id raises ValueError."""
        eval_set = EvalSet(eval_set_id="set1", eval_cases=[_make_eval_case("c1")])
        with pytest.raises(ValueError):
            add_eval_case_to_eval_set(eval_set, _make_eval_case("c1"))


class TestUpdateEvalCaseInEvalSet:
    """Test suite for update_eval_case_in_eval_set."""

    def test_updates_case(self):
        """Test update replaces case with same eval_id."""
        c1 = _make_eval_case("c1")
        eval_set = EvalSet(eval_set_id="set1", eval_cases=[c1])
        updated = _make_eval_case("c1")
        out = update_eval_case_in_eval_set(eval_set, updated)
        assert out is eval_set
        assert len(eval_set.eval_cases) == 1
        assert eval_set.eval_cases[0] is updated

    def test_not_found_raises(self):
        """Test updating non-existent eval_id raises NotFoundError."""
        eval_set = EvalSet(eval_set_id="set1", eval_cases=[])
        with pytest.raises(NotFoundError):
            update_eval_case_in_eval_set(eval_set, _make_eval_case("c1"))


class TestDeleteEvalCaseFromEvalSet:
    """Test suite for delete_eval_case_from_eval_set."""

    def test_deletes_case(self):
        """Test delete removes case."""
        c1 = _make_eval_case("c1")
        c2 = _make_eval_case("c2")
        eval_set = EvalSet(eval_set_id="set1", eval_cases=[c1, c2])
        out = delete_eval_case_from_eval_set(eval_set, "c1")
        assert out is eval_set
        assert len(eval_set.eval_cases) == 1
        assert eval_set.eval_cases[0].eval_id == "c2"

    def test_not_found_raises(self):
        """Test deleting non-existent eval_id raises NotFoundError."""
        eval_set = EvalSet(eval_set_id="set1", eval_cases=[])
        with pytest.raises(NotFoundError):
            delete_eval_case_from_eval_set(eval_set, "c1")
