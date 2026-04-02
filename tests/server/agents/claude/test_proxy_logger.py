# -*- coding: utf-8 -*-
"""Unit tests for ProxyLogger and get_proxy_logger."""

from __future__ import annotations

import logging
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from trpc_agent_sdk.log import LogLevel
from trpc_agent_sdk.server.agents.claude._proxy_logger import ProxyLogger, get_proxy_logger, _PROXY_LOGGER


@pytest.fixture(autouse=True)
def _reset_proxy_logger_singleton():
    """Reset the module-level singleton before each test."""
    import trpc_agent_sdk.server.agents.claude._proxy_logger as mod
    original = mod._PROXY_LOGGER
    mod._PROXY_LOGGER = None
    yield
    mod._PROXY_LOGGER = original


@pytest.fixture
def tmp_log_file(tmp_path):
    return str(tmp_path / "test_proxy.log")


class TestProxyLoggerInit:
    def test_default_init(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file)
        assert pl.name == "anthropic_proxy"
        assert pl._log_file == tmp_log_file
        assert pl.min_level == LogLevel.INFO

    def test_custom_init(self, tmp_log_file):
        pl = ProxyLogger(name="custom", log_file=tmp_log_file, min_level=LogLevel.DEBUG)
        assert pl.name == "custom"
        assert pl.min_level == LogLevel.DEBUG

    def test_console_handlers_removed(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file)
        for handler in pl.logger.handlers:
            assert not (isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler))

    def test_file_handler_added(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file)
        file_handlers = [h for h in pl.logger.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) >= 1

    def test_file_handler_not_duplicated(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file)
        initial_count = len([h for h in pl.logger.handlers if isinstance(h, logging.FileHandler)])
        pl._add_file_handler()
        after_count = len([h for h in pl.logger.handlers if isinstance(h, logging.FileHandler)])
        assert initial_count == after_count


class TestProxyLoggerLevelFiltering:
    def test_debug_not_logged_at_info_level(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file, min_level=LogLevel.INFO)
        pl.debug("should not appear")
        with open(tmp_log_file) as f:
            content = f.read()
        assert "should not appear" not in content

    def test_debug_logged_at_debug_level(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file, min_level=LogLevel.DEBUG)
        # Ensure the underlying Python logger level allows DEBUG
        pl.logger.setLevel(logging.DEBUG)
        pl.debug("debug message")
        # Flush handlers
        for h in pl.logger.handlers:
            h.flush()
        with open(tmp_log_file) as f:
            content = f.read()
        assert "debug message" in content

    def test_info_logged_at_info_level(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file, min_level=LogLevel.INFO)
        pl.info("info message")
        with open(tmp_log_file) as f:
            content = f.read()
        assert "info message" in content

    def test_warning_logged_at_info_level(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file, min_level=LogLevel.INFO)
        pl.warning("warning message")
        with open(tmp_log_file) as f:
            content = f.read()
        assert "warning message" in content

    def test_error_logged_at_info_level(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file, min_level=LogLevel.INFO)
        pl.error("error message")
        with open(tmp_log_file) as f:
            content = f.read()
        assert "error message" in content

    def test_fatal_logged_at_info_level(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file, min_level=LogLevel.INFO)
        pl.fatal("fatal message")
        with open(tmp_log_file) as f:
            content = f.read()
        assert "fatal message" in content

    def test_info_not_logged_at_warning_level(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file, min_level=LogLevel.WARNING)
        pl.info("should not appear")
        with open(tmp_log_file) as f:
            content = f.read()
        assert "should not appear" not in content

    def test_warning_not_logged_at_error_level(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file, min_level=LogLevel.ERROR)
        pl.warning("should not appear")
        with open(tmp_log_file) as f:
            content = f.read()
        assert "should not appear" not in content

    def test_error_not_logged_at_fatal_level(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file, min_level=LogLevel.FATAL)
        pl.error("should not appear")
        with open(tmp_log_file) as f:
            content = f.read()
        assert "should not appear" not in content


class TestProxyLoggerWithFields:
    def test_with_fields_returns_new_logger(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file)
        new_pl = pl.with_fields(request_id="123")
        assert new_pl is not pl
        assert isinstance(new_pl, ProxyLogger)

    def test_with_fields_preserves_existing_fields(self, tmp_log_file):
        pl = ProxyLogger(log_file=tmp_log_file)
        pl.extra_fields["foo"] = "bar"
        new_pl = pl.with_fields(baz="qux")
        assert new_pl.extra_fields["foo"] == "bar"
        assert new_pl.extra_fields["baz"] == "qux"

    def test_with_fields_preserves_config(self, tmp_log_file):
        pl = ProxyLogger(name="myname", log_file=tmp_log_file, min_level=LogLevel.DEBUG)
        new_pl = pl.with_fields(key="val")
        assert new_pl.name == "myname"
        assert new_pl.min_level == LogLevel.DEBUG


class TestGetProxyLogger:
    def test_returns_logger(self):
        with patch("trpc_agent_sdk.server.agents.claude._proxy_logger.ProxyLogger") as MockPL:
            mock_instance = MagicMock()
            mock_instance.name = "anthropic_proxy"
            MockPL.return_value = mock_instance

            result = get_proxy_logger()
            assert result is mock_instance

    def test_singleton_behavior(self):
        import trpc_agent_sdk.server.agents.claude._proxy_logger as mod
        with patch.object(mod, "ProxyLogger") as MockPL:
            mock_instance = MagicMock()
            mock_instance.name = "anthropic_proxy"
            MockPL.return_value = mock_instance

            first = get_proxy_logger()
            second = get_proxy_logger()
            assert first is second
            MockPL.assert_called_once()

    def test_set_as_default(self):
        with patch("trpc_agent_sdk.server.agents.claude._proxy_logger.ProxyLogger") as MockPL, \
             patch("trpc_agent_sdk.server.agents.claude._proxy_logger.set_default_logger") as mock_set:
            mock_instance = MagicMock()
            mock_instance.name = "anthropic_proxy"
            MockPL.return_value = mock_instance

            get_proxy_logger(set_as_default=True)
            mock_set.assert_called_once_with(mock_instance)

    def test_not_set_as_default_by_default(self):
        with patch("trpc_agent_sdk.server.agents.claude._proxy_logger.ProxyLogger") as MockPL, \
             patch("trpc_agent_sdk.server.agents.claude._proxy_logger.set_default_logger") as mock_set:
            mock_instance = MagicMock()
            mock_instance.name = "anthropic_proxy"
            MockPL.return_value = mock_instance

            get_proxy_logger(set_as_default=False)
            mock_set.assert_not_called()
