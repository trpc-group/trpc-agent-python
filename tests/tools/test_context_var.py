# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.abc import ToolABC
from trpc_agent_sdk.tools._context_var import get_tool_var, reset_tool_var, set_tool_var


class TestContextVar:

    def test_set_and_get_tool_var(self):
        mock_tool = MagicMock(spec=ToolABC)
        token = set_tool_var(mock_tool)
        try:
            result = get_tool_var()
            assert result is mock_tool
        finally:
            reset_tool_var(token)

    def test_reset_restores_previous(self):
        mock_tool_1 = MagicMock(spec=ToolABC)
        mock_tool_2 = MagicMock(spec=ToolABC)

        token_1 = set_tool_var(mock_tool_1)
        assert get_tool_var() is mock_tool_1

        token_2 = set_tool_var(mock_tool_2)
        assert get_tool_var() is mock_tool_2

        reset_tool_var(token_2)
        assert get_tool_var() is mock_tool_1

        reset_tool_var(token_1)

    def test_reset_with_invalid_token_raises(self):
        mock_tool = MagicMock(spec=ToolABC)
        token = set_tool_var(mock_tool)
        reset_tool_var(token)
        # Second reset with the same (used) token raises RuntimeError
        # The source only catches ValueError, not RuntimeError
        with pytest.raises(RuntimeError):
            reset_tool_var(token)

    def test_get_default_is_none(self):
        # In a fresh context, the default should be None
        # We need to set and reset to verify the default behavior
        mock_tool = MagicMock(spec=ToolABC)
        token = set_tool_var(mock_tool)
        reset_tool_var(token)
        result = get_tool_var()
        assert result is None
