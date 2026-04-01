# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for graph interrupt wrapper."""

from unittest.mock import patch

from trpc_agent_sdk.dsl.graph._interrupt import interrupt


class TestInterrupt:
    """Tests for interrupt passthrough behavior."""

    def test_interrupt_delegates_to_langgraph_interrupt(self):
        """Wrapper should call langgraph interrupt function with original payload."""
        payload = {"need": "approval"}

        with patch("trpc_agent_dsl.graph._interrupt._langgraph_interrupt", return_value={"resume": True}) as mock_fn:
            result = interrupt(payload)

        mock_fn.assert_called_once_with(payload)
        assert result == {"resume": True}
