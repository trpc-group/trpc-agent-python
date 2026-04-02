# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from unittest.mock import MagicMock

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools._transfer_to_agent_tool import transfer_to_agent


class TestTransferToAgent:

    def test_sets_transfer_action(self):
        mock_ctx = MagicMock(spec=InvocationContext)
        mock_ctx.actions = MagicMock()

        transfer_to_agent("target_agent", mock_ctx)

        assert mock_ctx.actions.transfer_to_agent == "target_agent"

    def test_returns_dict_with_agent_name(self):
        mock_ctx = MagicMock(spec=InvocationContext)
        mock_ctx.actions = MagicMock()

        result = transfer_to_agent("my_agent", mock_ctx)

        assert result == {"transferred_to": "my_agent"}

    def test_with_different_agent_names(self):
        for name in ["agent_a", "agent_b", "helper", ""]:
            mock_ctx = MagicMock(spec=InvocationContext)
            mock_ctx.actions = MagicMock()

            result = transfer_to_agent(name, mock_ctx)

            assert mock_ctx.actions.transfer_to_agent == name
            assert result == {"transferred_to": name}
