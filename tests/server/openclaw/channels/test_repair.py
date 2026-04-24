# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.openclaw.channels._repair."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.server.openclaw.channels._repair import (
    _channels_to_repair,
    register_channel_repair,
    repair_channels,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure the module-level registry is clean between tests."""
    saved = dict(_channels_to_repair)
    _channels_to_repair.clear()
    yield
    _channels_to_repair.clear()
    _channels_to_repair.update(saved)


# ---------------------------------------------------------------------------
# register_channel_repair
# ---------------------------------------------------------------------------
class TestRegisterChannelRepair:

    def test_adds_to_registry(self):
        fn = MagicMock()
        register_channel_repair("test_chan", fn)
        assert _channels_to_repair["test_chan"] is fn

    def test_overwrites_existing(self):
        fn1 = MagicMock()
        fn2 = MagicMock()
        register_channel_repair("chan", fn1)
        register_channel_repair("chan", fn2)
        assert _channels_to_repair["chan"] is fn2

    def test_multiple_channels(self):
        fn_a = MagicMock()
        fn_b = MagicMock()
        register_channel_repair("a", fn_a)
        register_channel_repair("b", fn_b)
        assert len(_channels_to_repair) == 2
        assert _channels_to_repair["a"] is fn_a
        assert _channels_to_repair["b"] is fn_b


# ---------------------------------------------------------------------------
# repair_channels
# ---------------------------------------------------------------------------
class TestRepairChannels:

    def test_calls_all_registered_functions(self):
        fn_a = MagicMock()
        fn_b = MagicMock()
        register_channel_repair("alpha", fn_a)
        register_channel_repair("beta", fn_b)

        mgr = MagicMock()
        repair_channels(mgr)

        fn_a.assert_called_once_with("alpha", mgr)
        fn_b.assert_called_once_with("beta", mgr)

    def test_empty_registry_does_nothing(self):
        mgr = MagicMock()
        repair_channels(mgr)

    def test_exception_does_not_stop_other_repairs(self):
        fn_fail = MagicMock(side_effect=RuntimeError("boom"))
        fn_ok = MagicMock()
        register_channel_repair("bad", fn_fail)
        register_channel_repair("good", fn_ok)

        mgr = MagicMock()
        repair_channels(mgr)

        fn_fail.assert_called_once_with("bad", mgr)
        fn_ok.assert_called_once_with("good", mgr)

    @patch("trpc_agent_sdk.server.openclaw.channels._repair.logger")
    def test_exception_is_logged(self, mock_logger):
        fn = MagicMock(side_effect=ValueError("oops"))
        register_channel_repair("broken", fn)

        mgr = MagicMock()
        repair_channels(mgr)

        mock_logger.error.assert_called_once()
        assert "broken" in str(mock_logger.error.call_args)
