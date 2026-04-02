# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for UserSimulatorProvider."""

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import StaticUserSimulator
from trpc_agent_sdk.evaluation import EvalCase
from trpc_agent_sdk.evaluation import Invocation
from trpc_agent_sdk.evaluation import SessionInput
from trpc_agent_sdk.evaluation._user_simulator_base import BaseUserSimulatorConfig
from trpc_agent_sdk.evaluation._user_simulator_provider import UserSimulatorProvider
from trpc_agent_sdk.types import Content


class TestUserSimulatorProvider:
    """Test suite for UserSimulatorProvider."""

    def test_default_config(self):
        """Test default config creates provider."""
        p = UserSimulatorProvider()
        assert p is not None

    def test_explicit_config(self):
        """Test explicit BaseUserSimulatorConfig."""
        cfg = BaseUserSimulatorConfig()
        p = UserSimulatorProvider(user_simulator_config=cfg)
        assert p is not None

    def test_invalid_config_raises(self):
        """Test invalid config type raises ValueError."""
        with pytest.raises(ValueError, match="Expect config"):
            UserSimulatorProvider(user_simulator_config="bad")

    def test_provide_with_conversation(self):
        """Test provide returns StaticUserSimulator for conversation."""
        p = UserSimulatorProvider()
        case = EvalCase(
            eval_id="c1",
            conversation=[Invocation(user_content=Content(parts=[]))],
            session_input=SessionInput(app_name="a", user_id="u", state={}),
        )
        sim = p.provide(case)
        assert isinstance(sim, StaticUserSimulator)

    def test_provide_no_conversation_raises(self):
        """Test provide with empty conversation raises ValueError."""
        from unittest.mock import MagicMock
        p = UserSimulatorProvider()
        case = MagicMock()
        case.conversation = None
        with pytest.raises(ValueError, match="No conversation"):
            p.provide(case)
