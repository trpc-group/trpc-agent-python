# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for AgentABC.

Covers concrete methods in AgentABC:
- root_agent property
- get_agent_class / get_agent_type_name
- find_agent / find_sub_agent (recursive search)
- model_post_init parent-agent wiring and duplicate-parent detection
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

import pytest

from trpc_agent_sdk.abc._agent import AgentABC


class StubAgent(AgentABC):
    """Minimal concrete agent for testing AgentABC logic."""

    def get_subagents(self) -> list[AgentABC]:
        return list(self.sub_agents)

    async def run_async(self, parent_context) -> AsyncGenerator[Any, None]:
        yield  # pragma: no cover


@pytest.fixture
def leaf_agent():
    return StubAgent(name="leaf")


@pytest.fixture
def mid_agent(leaf_agent):
    return StubAgent(name="mid", sub_agents=[leaf_agent])


@pytest.fixture
def root_agent(mid_agent):
    return StubAgent(name="root", sub_agents=[mid_agent])


class TestRootAgent:
    """Tests for the root_agent property."""

    def test_root_of_leaf_is_root(self, root_agent, mid_agent, leaf_agent):
        assert leaf_agent.root_agent is root_agent

    def test_root_of_mid_is_root(self, root_agent, mid_agent):
        assert mid_agent.root_agent is root_agent

    def test_root_of_root_is_itself(self, root_agent):
        assert root_agent.root_agent is root_agent

    def test_standalone_agent_is_its_own_root(self):
        agent = StubAgent(name="solo")
        assert agent.root_agent is agent


class TestGetAgentClass:
    """Tests for get_agent_class and get_agent_type_name."""

    def test_get_agent_class_returns_concrete_type(self, leaf_agent):
        assert leaf_agent.get_agent_class() is StubAgent

    def test_get_agent_type_name_returns_class_name(self, leaf_agent):
        assert leaf_agent.get_agent_type_name() == "StubAgent"


class TestFindAgent:
    """Tests for find_agent (searches self + descendants)."""

    def test_find_self_by_name(self, root_agent):
        assert root_agent.find_agent("root") is root_agent

    def test_find_direct_child(self, root_agent, mid_agent):
        assert root_agent.find_agent("mid") is mid_agent

    def test_find_deep_descendant(self, root_agent, leaf_agent):
        assert root_agent.find_agent("leaf") is leaf_agent

    def test_find_returns_none_for_nonexistent(self, root_agent):
        assert root_agent.find_agent("nonexistent") is None


class TestFindSubAgent:
    """Tests for find_sub_agent (searches descendants only, not self)."""

    def test_does_not_find_self(self, root_agent):
        assert root_agent.find_sub_agent("root") is None

    def test_finds_child(self, root_agent, mid_agent):
        assert root_agent.find_sub_agent("mid") is mid_agent

    def test_finds_grandchild(self, root_agent, leaf_agent):
        assert root_agent.find_sub_agent("leaf") is leaf_agent

    def test_returns_none_when_no_sub_agents(self, leaf_agent):
        assert leaf_agent.find_sub_agent("anything") is None

    def test_returns_first_match_in_breadth(self):
        """When multiple branches exist, the first matching sub_agent wins."""
        a = StubAgent(name="target")
        b = StubAgent(name="other")
        parent = StubAgent(name="parent", sub_agents=[a, b])
        assert parent.find_sub_agent("target") is a


class TestModelPostInit:
    """Tests for parent-agent wiring in model_post_init."""

    def test_parent_set_for_sub_agents(self, root_agent, mid_agent, leaf_agent):
        assert mid_agent.parent_agent is root_agent
        assert leaf_agent.parent_agent is mid_agent

    def test_root_has_no_parent(self, root_agent):
        assert root_agent.parent_agent is None

    def test_duplicate_parent_raises_value_error(self):
        child = StubAgent(name="child")
        StubAgent(name="parent1", sub_agents=[child])
        with pytest.raises(ValueError, match="already has a parent agent"):
            StubAgent(name="parent2", sub_agents=[child])

    def test_empty_sub_agents_ok(self):
        agent = StubAgent(name="no_children", sub_agents=[])
        assert agent.sub_agents == []
        assert agent.parent_agent is None


class TestDisallowFlags:
    """Tests for disallow_transfer_to_parent and disallow_transfer_to_peers."""

    def test_defaults_are_false(self):
        agent = StubAgent(name="a")
        assert agent.disallow_transfer_to_parent is False
        assert agent.disallow_transfer_to_peers is False

    def test_can_set_true(self):
        agent = StubAgent(
            name="a",
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
        )
        assert agent.disallow_transfer_to_parent is True
        assert agent.disallow_transfer_to_peers is True
