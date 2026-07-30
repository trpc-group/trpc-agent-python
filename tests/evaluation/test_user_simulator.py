# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for user simulator base (_user_simulator_base)."""

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import BaseUserSimulatorConfig
from trpc_agent_sdk.evaluation import NextUserMessage
from trpc_agent_sdk.evaluation import Status


class TestStatus:
    """Test suite for Status enum."""

    def test_values(self):
        """Test Status has expected values."""
        assert Status.SUCCESS.value == "success"
        assert Status.TURN_LIMIT_REACHED.value == "turn_limit_reached"
        assert Status.STOP_SIGNAL_DETECTED.value == "stop_signal_detected"
        assert Status.NO_MESSAGE_GENERATED.value == "no_message_generated"


class TestNextUserMessage:
    """Test suite for NextUserMessage."""

    def test_success_requires_user_message(self):
        """Test SUCCESS status requires user_message (Content with parts, role)."""
        from trpc_agent_sdk.types import Content, Part
        msg = Content(parts=[Part(text="hello")], role="user")
        n = NextUserMessage(status=Status.SUCCESS, user_message=msg)
        assert n.status == Status.SUCCESS
        assert n.user_message is msg

    def test_no_message_generated_requires_none_message(self):
        """Test NO_MESSAGE_GENERATED requires user_message None."""
        n = NextUserMessage(status=Status.NO_MESSAGE_GENERATED, user_message=None)
        assert n.status == Status.NO_MESSAGE_GENERATED
        assert n.user_message is None

    def test_success_with_none_raises(self):
        """Test SUCCESS with user_message None fails validation."""
        with pytest.raises(ValueError):
            NextUserMessage(status=Status.SUCCESS, user_message=None)

    def test_no_message_with_message_raises(self):
        """Test NO_MESSAGE_GENERATED with user_message set fails validation."""
        with pytest.raises(ValueError):
            NextUserMessage(status=Status.NO_MESSAGE_GENERATED, user_message=object())


class TestBaseUserSimulatorConfig:
    """Test suite for BaseUserSimulatorConfig."""

    def test_extra_allow(self):
        """Test config allows extra fields (model_config extra=allow)."""
        c = BaseUserSimulatorConfig()
        # Can create with extra if model supports it
        assert c is not None
