# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for StaticUserSimulator."""

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import StaticUserSimulator
from trpc_agent_sdk.evaluation import Status
from trpc_agent_sdk.evaluation import Invocation
from trpc_agent_sdk.types import Content


def _make_invocation(text):
    return Invocation(user_content=Content(parts=[type("P", (), {"text": text})()]))


class TestStaticUserSimulator:
    """Test suite for StaticUserSimulator."""

    async def test_returns_messages_in_order(self):
        """Test returns user messages in sequence."""
        conv = [_make_invocation("hello"), _make_invocation("how are you")]
        sim = StaticUserSimulator(static_conversation=conv)
        msg1 = await sim.get_next_user_message([])
        assert msg1.status == Status.SUCCESS
        msg2 = await sim.get_next_user_message([])
        assert msg2.status == Status.SUCCESS

    async def test_stop_at_end(self):
        """Test returns STOP_SIGNAL_DETECTED after all messages consumed."""
        conv = [_make_invocation("hi")]
        sim = StaticUserSimulator(static_conversation=conv)
        await sim.get_next_user_message([])
        msg = await sim.get_next_user_message([])
        assert msg.status == Status.STOP_SIGNAL_DETECTED

    async def test_empty_conversation(self):
        """Test empty conversation immediately stops."""
        sim = StaticUserSimulator(static_conversation=[])
        msg = await sim.get_next_user_message([])
        assert msg.status == Status.STOP_SIGNAL_DETECTED

    def test_get_simulation_evaluator_returns_none(self):
        """Test get_simulation_evaluator returns None."""
        sim = StaticUserSimulator(static_conversation=[])
        assert sim.get_simulation_evaluator() is None
