# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for graph interrupt wrapper."""

from unittest.mock import patch

from trpc_agent_sdk.dsl.graph._interrupt import interrupt


class TestInterrupt:
    """Tests for interrupt passthrough behavior."""

    def test_interrupt_delegates_to_langgraph_interrupt(self):
        """Wrapper should call langgraph interrupt function with original payload."""
        payload = {"need": "approval"}

        with patch("trpc_agent_sdk.dsl.graph._interrupt._langgraph_interrupt", return_value={"resume": True}) as mock_fn:
            result = interrupt(payload)

        mock_fn.assert_called_once_with(payload)
        assert result == {"resume": True}
