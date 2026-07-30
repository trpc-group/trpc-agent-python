# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for LocalEvalSetsManager and load_eval_set_from_file."""

import json
import os
import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import EvalCase
from trpc_agent_sdk.evaluation import EvalSet
from trpc_agent_sdk.evaluation import SessionInput
from trpc_agent_sdk.evaluation import Invocation
from trpc_agent_sdk.evaluation._local_eval_sets_manager import (
    LocalEvalSetsManager,
    load_eval_set_from_file,
)
from trpc_agent_sdk.types import Content


def _make_case_dict(eval_id="c1"):
    return {
        "eval_id": eval_id,
        "conversation": [{"user_content": {"parts": []}}],
        "session_input": {"app_name": "a", "user_id": "u", "state": {}},
    }


class TestLoadEvalSetFromFile:
    """Test suite for load_eval_set_from_file."""

    def test_load_valid_file(self, tmp_path):
        """Test loading a valid eval set file."""
        data = {"eval_set_id": "s1", "eval_cases": [_make_case_dict("c1")]}
        p = tmp_path / "test.evalset.json"
        p.write_text(json.dumps(data))
        result = load_eval_set_from_file(str(p), "s1")
        assert isinstance(result, EvalSet)
        assert result.eval_set_id == "s1"
        assert len(result.eval_cases) == 1

    def test_load_list_format(self, tmp_path):
        """Test loading old list format (list of eval cases)."""
        data = [_make_case_dict("c1"), _make_case_dict("c2")]
        p = tmp_path / "test.evalset.json"
        p.write_text(json.dumps(data))
        result = load_eval_set_from_file(str(p), "s1")
        assert isinstance(result, EvalSet)
        assert len(result.eval_cases) == 2


class TestLocalEvalSetsManagerGetEvalSet:
    """Test suite for LocalEvalSetsManager.get_eval_set."""

    def test_returns_none_for_missing(self, tmp_path):
        """Test returns None when eval set file doesn't exist."""
        m = LocalEvalSetsManager(str(tmp_path))
        assert m.get_eval_set("app1", "nonexistent") is None

    def test_returns_loaded_set(self, tmp_path):
        """Test returns loaded eval set from file."""
        app_dir = tmp_path / "app1"
        app_dir.mkdir()
        data = {"eval_set_id": "s1", "eval_cases": [_make_case_dict("c1")]}
        (app_dir / "s1.evalset.json").write_text(json.dumps(data))
        m = LocalEvalSetsManager(str(tmp_path))
        result = m.get_eval_set("app1", "s1")
        assert result is not None
        assert result.eval_set_id == "s1"


class TestLocalEvalSetsManagerCreateEvalSet:
    """Test suite for LocalEvalSetsManager.create_eval_set."""

    def test_creates_new_set(self, tmp_path):
        """Test creates new eval set file."""
        m = LocalEvalSetsManager(str(tmp_path))
        s = m.create_eval_set("app1", "new_set")
        assert isinstance(s, EvalSet)
        assert os.path.exists(os.path.join(str(tmp_path), "app1", "new_set.evalset.json"))

    def test_duplicate_raises(self, tmp_path):
        """Test creating duplicate raises ValueError."""
        m = LocalEvalSetsManager(str(tmp_path))
        m.create_eval_set("app1", "s1")
        with pytest.raises(ValueError, match="already exists"):
            m.create_eval_set("app1", "s1")


class TestLocalEvalSetsManagerListEvalSets:
    """Test suite for LocalEvalSetsManager.list_eval_sets."""

    def test_empty_dir(self, tmp_path):
        """Test list on empty dir returns empty."""
        m = LocalEvalSetsManager(str(tmp_path))
        assert m.list_eval_sets("app1") == []

    def test_lists_sets(self, tmp_path):
        """Test lists created eval sets."""
        m = LocalEvalSetsManager(str(tmp_path))
        m.create_eval_set("app1", "s1")
        m.create_eval_set("app1", "s2")
        result = m.list_eval_sets("app1")
        assert set(result) == {"s1", "s2"}


class TestLocalEvalSetsManagerCaseCrud:
    """Test suite for case CRUD operations."""

    def test_add_and_get_case(self, tmp_path):
        """Test add and get eval case."""
        m = LocalEvalSetsManager(str(tmp_path))
        m.create_eval_set("app1", "s1")
        case = EvalCase(
            eval_id="c1",
            conversation=[Invocation(user_content=Content(parts=[]))],
            session_input=SessionInput(app_name="a", user_id="u", state={}),
        )
        m.add_eval_case("app1", "s1", case)
        result = m.get_eval_case("app1", "s1", "c1")
        assert result is not None
        assert result.eval_id == "c1"

    def test_update_case(self, tmp_path):
        """Test update eval case."""
        m = LocalEvalSetsManager(str(tmp_path))
        m.create_eval_set("app1", "s1")
        case = EvalCase(
            eval_id="c1",
            conversation=[Invocation(user_content=Content(parts=[]))],
            session_input=SessionInput(app_name="a", user_id="u", state={}),
        )
        m.add_eval_case("app1", "s1", case)
        updated = EvalCase(
            eval_id="c1",
            conversation=[Invocation(user_content=Content(parts=[]))],
            session_input=SessionInput(app_name="updated", user_id="u", state={}),
        )
        m.update_eval_case("app1", "s1", updated)
        result = m.get_eval_case("app1", "s1", "c1")
        assert result.session_input.app_name == "updated"

    def test_delete_case(self, tmp_path):
        """Test delete eval case."""
        m = LocalEvalSetsManager(str(tmp_path))
        m.create_eval_set("app1", "s1")
        case = EvalCase(
            eval_id="c1",
            conversation=[Invocation(user_content=Content(parts=[]))],
            session_input=SessionInput(app_name="a", user_id="u", state={}),
        )
        m.add_eval_case("app1", "s1", case)
        m.delete_eval_case("app1", "s1", "c1")
        assert m.get_eval_case("app1", "s1", "c1") is None


class TestLocalEvalSetsManagerValidateId:
    """Test suite for _validate_id."""

    def test_invalid_id_raises(self, tmp_path):
        """Test invalid id raises ValueError."""
        m = LocalEvalSetsManager(str(tmp_path))
        with pytest.raises(ValueError):
            m.create_eval_set("app1", "../../bad")
