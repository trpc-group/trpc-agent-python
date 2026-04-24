# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.openclaw._logger."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import trpc_agent_sdk.server.openclaw._logger as _logger_mod
from trpc_agent_sdk.server.openclaw._logger import (
    default_logger,
    init_claw_logger,
    setup_loguru_bridge,
)


@pytest.fixture(autouse=True)
def _reset_bridge_flag():
    """Reset module-level bridge flag between tests."""
    _logger_mod._LOGURU_BRIDGE_ENABLED = False
    yield
    _logger_mod._LOGURU_BRIDGE_ENABLED = False


# ---------------------------------------------------------------------------
# setup_loguru_bridge
# ---------------------------------------------------------------------------
class TestSetupLoguruBridge:

    @patch.object(_logger_mod, "_loguru_logger")
    def test_sets_flag_on_first_call(self, mock_loguru):
        assert _logger_mod._LOGURU_BRIDGE_ENABLED is False
        setup_loguru_bridge()
        assert _logger_mod._LOGURU_BRIDGE_ENABLED is True
        mock_loguru.remove.assert_called_once()
        mock_loguru.add.assert_called_once()

    @patch.object(_logger_mod, "_loguru_logger")
    def test_idempotent_second_call(self, mock_loguru):
        setup_loguru_bridge()
        mock_loguru.reset_mock()
        setup_loguru_bridge()
        mock_loguru.remove.assert_not_called()
        mock_loguru.add.assert_not_called()

    @patch("trpc_agent_sdk.server.openclaw._logger.logger")
    @patch.object(_logger_mod, "_loguru_logger")
    def test_sink_trace_routes_to_debug(self, mock_loguru, mock_logger):
        setup_loguru_bridge()
        sink_fn = mock_loguru.add.call_args[0][0]

        record = {
            "level": MagicMock(name="TRACE"),
            "message": "trace msg",
            "file": MagicMock(path="test.py"),
            "line": 42,
        }
        record["level"].name = "TRACE"
        message = MagicMock()
        message.record = record

        sink_fn(message)
        mock_logger.debug.assert_called_once()
        assert "trace msg" in str(mock_logger.debug.call_args)

    @patch("trpc_agent_sdk.server.openclaw._logger.logger")
    @patch.object(_logger_mod, "_loguru_logger")
    def test_sink_debug_routes_to_debug(self, mock_loguru, mock_logger):
        setup_loguru_bridge()
        sink_fn = mock_loguru.add.call_args[0][0]

        record = {
            "level": MagicMock(name="DEBUG"),
            "message": "debug msg",
            "file": MagicMock(path="test.py"),
            "line": 10,
        }
        record["level"].name = "DEBUG"
        message = MagicMock()
        message.record = record

        sink_fn(message)
        mock_logger.debug.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw._logger.logger")
    @patch.object(_logger_mod, "_loguru_logger")
    def test_sink_info_routes_to_info(self, mock_loguru, mock_logger):
        setup_loguru_bridge()
        sink_fn = mock_loguru.add.call_args[0][0]

        record = {
            "level": MagicMock(name="INFO"),
            "message": "info msg",
            "file": MagicMock(path="test.py"),
            "line": 20,
        }
        record["level"].name = "INFO"
        message = MagicMock()
        message.record = record

        sink_fn(message)
        mock_logger.info.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw._logger.logger")
    @patch.object(_logger_mod, "_loguru_logger")
    def test_sink_warning_routes_to_warning(self, mock_loguru, mock_logger):
        setup_loguru_bridge()
        sink_fn = mock_loguru.add.call_args[0][0]

        record = {
            "level": MagicMock(name="WARNING"),
            "message": "warn msg",
            "file": MagicMock(path="test.py"),
            "line": 30,
        }
        record["level"].name = "WARNING"
        message = MagicMock()
        message.record = record

        sink_fn(message)
        mock_logger.warning.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw._logger.logger")
    @patch.object(_logger_mod, "_loguru_logger")
    def test_sink_error_routes_to_error(self, mock_loguru, mock_logger):
        setup_loguru_bridge()
        sink_fn = mock_loguru.add.call_args[0][0]

        record = {
            "level": MagicMock(name="ERROR"),
            "message": "error msg",
            "file": MagicMock(path="test.py"),
            "line": 50,
        }
        record["level"].name = "ERROR"
        message = MagicMock()
        message.record = record

        sink_fn(message)
        mock_logger.error.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw._logger.logger")
    @patch.object(_logger_mod, "_loguru_logger")
    def test_sink_empty_message_skipped(self, mock_loguru, mock_logger):
        setup_loguru_bridge()
        sink_fn = mock_loguru.add.call_args[0][0]

        record = {
            "level": MagicMock(name="INFO"),
            "message": "   ",
            "file": MagicMock(path="test.py"),
            "line": 1,
        }
        record["level"].name = "INFO"
        message = MagicMock()
        message.record = record

        sink_fn(message)
        mock_logger.info.assert_not_called()
        mock_logger.debug.assert_not_called()

    @patch("trpc_agent_sdk.server.openclaw._logger.logger")
    @patch.object(_logger_mod, "_loguru_logger")
    def test_sink_no_file_uses_unknown(self, mock_loguru, mock_logger):
        setup_loguru_bridge()
        sink_fn = mock_loguru.add.call_args[0][0]

        record = {
            "level": MagicMock(name="INFO"),
            "message": "msg",
            "file": None,
            "line": 0,
        }
        record["level"].name = "INFO"
        message = MagicMock()
        message.record = record

        sink_fn(message)
        call_text = str(mock_logger.info.call_args)
        assert "unknown" in call_text


# ---------------------------------------------------------------------------
# default_logger
# ---------------------------------------------------------------------------
class TestDefaultLogger:

    @patch("trpc_agent_sdk.server.openclaw._logger.logging.FileHandler")
    def test_returns_default_logger_with_file_handler(self, mock_fh_cls):
        mock_handler = MagicMock()
        mock_fh_cls.return_value = mock_handler

        result = default_logger()

        from trpc_agent_sdk.log import DefaultLogger
        assert isinstance(result, DefaultLogger)
        mock_fh_cls.assert_called_once_with("trpc_claw.log", encoding="utf-8")
        mock_handler.setFormatter.assert_called_once()
        assert mock_handler in result.logger.handlers


# ---------------------------------------------------------------------------
# init_claw_logger
# ---------------------------------------------------------------------------
class TestInitClawLogger:

    @patch("trpc_agent_sdk.server.openclaw._logger.setup_loguru_bridge")
    @patch("trpc_agent_sdk.server.openclaw._logger.set_logger")
    @patch("trpc_agent_sdk.server.openclaw._logger.logging.FileHandler")
    def test_creates_logger_with_file(self, mock_fh_cls, mock_set_logger, mock_bridge):
        mock_handler = MagicMock()
        mock_fh_cls.return_value = mock_handler

        config = MagicMock()
        config.log_level = "DEBUG"
        config.name = "test_claw"
        config.log_file = "/tmp/test.log"
        config.log_format = "%(message)s"

        result = init_claw_logger(config)

        from trpc_agent_sdk.log import DefaultLogger
        assert isinstance(result, DefaultLogger)
        mock_fh_cls.assert_called_once_with("/tmp/test.log", encoding="utf-8")
        mock_handler.setFormatter.assert_called_once()
        mock_set_logger.assert_called_once_with(result)
        mock_bridge.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw._logger.setup_loguru_bridge")
    @patch("trpc_agent_sdk.server.openclaw._logger.set_logger")
    @patch("trpc_agent_sdk.server.openclaw._logger.logging.FileHandler")
    def test_no_file_handler_when_log_file_empty(self, mock_fh_cls, mock_set_logger, mock_bridge):
        config = MagicMock()
        config.log_level = "INFO"
        config.name = "no_file"
        config.log_file = ""

        result = init_claw_logger(config)

        mock_fh_cls.assert_not_called()
        mock_set_logger.assert_called_once_with(result)
        mock_bridge.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw._logger.setup_loguru_bridge")
    @patch("trpc_agent_sdk.server.openclaw._logger.set_logger")
    @patch("trpc_agent_sdk.server.openclaw._logger.logging.FileHandler")
    def test_log_level_case_insensitive(self, mock_fh_cls, mock_set_logger, mock_bridge):
        config = MagicMock()
        config.log_level = "warning"
        config.name = "warn_claw"
        config.log_file = ""

        result = init_claw_logger(config)

        from trpc_agent_sdk.log import DefaultLogger
        assert isinstance(result, DefaultLogger)
