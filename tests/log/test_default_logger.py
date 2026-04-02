# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for _default_logger module (RelativePathFormatter and DefaultLogger)."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.log._base_logger import BaseLogger, LogLevel
from trpc_agent_sdk.log._default_logger import DefaultLogger, RelativePathFormatter


# ---------------------------------------------------------------------------
# RelativePathFormatter
# ---------------------------------------------------------------------------


class TestRelativePathFormatterInit:
    def test_explicit_base_path(self):
        fmt = RelativePathFormatter(base_path="/some/base")
        assert fmt.base_path == "/some/base"

    def test_auto_detect_base_path(self):
        fmt = RelativePathFormatter()
        assert isinstance(fmt.base_path, str)
        assert len(fmt.base_path) > 0


class TestRelativePathFormatterDetectBasePath:
    def test_detect_from_site_packages(self, tmp_path):
        fake_site = str(tmp_path / "venv" / "lib" / "python3.11" / "site-packages")
        os.makedirs(fake_site, exist_ok=True)
        project_root = str(tmp_path)

        with patch("site.getsitepackages", return_value=[fake_site]):
            fmt = RelativePathFormatter()
            assert fmt.base_path == project_root

    def test_fallback_to_cwd_when_site_raises(self):
        with patch("site.getsitepackages", side_effect=Exception("no site")):
            fmt = RelativePathFormatter()
            assert fmt.base_path == os.getcwd()

    def test_fallback_to_cwd_when_site_returns_empty(self):
        with patch("site.getsitepackages", return_value=[]):
            fmt = RelativePathFormatter()
            assert fmt.base_path == os.getcwd()

    def test_fallback_when_project_root_not_dir(self):
        with patch("site.getsitepackages", return_value=["/nonexistent/venv/lib/py/site-packages"]):
            with patch("os.path.isdir", return_value=False):
                fmt = RelativePathFormatter()
                assert fmt.base_path == os.getcwd()


class TestRelativePathFormatterFormat:
    @pytest.fixture
    def formatter(self, tmp_path):
        return RelativePathFormatter(
            fmt="%(pathname)s:%(lineno)d - %(message)s",
            base_path=str(tmp_path),
        )

    def test_relative_path_inside_base(self, formatter, tmp_path):
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=str(tmp_path / "sub" / "file.py"),
            lineno=42,
            msg="hello",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        assert result.startswith(os.path.join("sub", "file.py"))
        assert ":42" in result

    def test_absolute_path_outside_base(self, formatter, tmp_path):
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/completely/different/path/file.py",
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        assert "/completely/different/path/file.py" in result

    def test_empty_pathname(self, formatter):
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        assert "msg" in result

    def test_none_base_path(self):
        fmt = RelativePathFormatter.__new__(RelativePathFormatter)
        fmt._fmt = "%(pathname)s - %(message)s"
        fmt._style = logging.PercentStyle(fmt._fmt)
        fmt.datefmt = None
        fmt.base_path = None
        fmt.defaults = {}

        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="/some/path/file.py", lineno=1,
            msg="msg", args=(), exc_info=None,
        )
        result = fmt.format(record)
        assert "/some/path/file.py" in result

    def test_format_with_datefmt(self, tmp_path):
        fmt = RelativePathFormatter(
            fmt="%(asctime)s %(message)s",
            datefmt="%Y-%m-%d",
            base_path=str(tmp_path),
        )
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname=str(tmp_path / "f.py"), lineno=1,
            msg="msg", args=(), exc_info=None,
        )
        result = fmt.format(record)
        assert "msg" in result


# ---------------------------------------------------------------------------
# DefaultLogger
# ---------------------------------------------------------------------------


class TestDefaultLoggerInit:
    def test_default_params(self):
        logger = DefaultLogger(name="test_init_default")
        assert logger.name == "test_init_default"
        assert logger.min_level == LogLevel.INFO
        assert logger.extra_fields == {}

    def test_custom_params(self):
        fields = {"service": "api"}
        logger = DefaultLogger(name="test_init_custom", min_level=LogLevel.DEBUG, extra_fields=fields)
        assert logger.min_level == LogLevel.DEBUG
        assert logger.extra_fields == {"service": "api"}

    def test_logger_is_python_logger(self):
        logger = DefaultLogger(name="test_init_pylogger")
        assert isinstance(logger.logger, logging.Logger)

    def test_handler_added(self):
        name = "test_init_handler_check"
        logging.getLogger(name).handlers.clear()
        logger = DefaultLogger(name=name)
        assert len(logger.logger.handlers) >= 1

    def test_propagate_disabled(self):
        name = "test_init_propagate"
        logging.getLogger(name).handlers.clear()
        logger = DefaultLogger(name=name)
        assert logger.logger.propagate is False

    def test_no_duplicate_handlers(self):
        name = "test_init_no_dup"
        logging.getLogger(name).handlers.clear()
        logger1 = DefaultLogger(name=name)
        handler_count = len(logger1.logger.handlers)
        logger2 = DefaultLogger(name=name)
        assert len(logger2.logger.handlers) == handler_count


class TestDefaultLoggerConvertLogLevel:
    @pytest.fixture
    def logger(self):
        return DefaultLogger(name="test_convert_level")

    def test_trace_maps_to_debug(self, logger):
        assert logger._convert_log_level(LogLevel.TRACE) == logging.DEBUG

    def test_debug_maps_to_debug(self, logger):
        assert logger._convert_log_level(LogLevel.DEBUG) == logging.DEBUG

    def test_info_maps_to_info(self, logger):
        assert logger._convert_log_level(LogLevel.INFO) == logging.INFO

    def test_warning_maps_to_warning(self, logger):
        assert logger._convert_log_level(LogLevel.WARNING) == logging.WARNING

    def test_error_maps_to_error(self, logger):
        assert logger._convert_log_level(LogLevel.ERROR) == logging.ERROR

    def test_fatal_maps_to_critical(self, logger):
        assert logger._convert_log_level(LogLevel.FATAL) == logging.CRITICAL


class TestDefaultLoggerEnsureExtraFields:
    def test_adds_extra_when_missing(self):
        logger = DefaultLogger(name="test_ensure_extra1")
        result = logger._ensure_extra_fields()
        assert "extra" in result

    def test_merges_instance_fields(self):
        logger = DefaultLogger(name="test_ensure_extra2", extra_fields={"region": "us"})
        result = logger._ensure_extra_fields()
        assert result["extra"]["region"] == "us"

    def test_preserves_existing_extra(self):
        logger = DefaultLogger(name="test_ensure_extra3", extra_fields={"a": 1})
        result = logger._ensure_extra_fields(extra={"b": 2})
        assert result["extra"]["a"] == 1
        assert result["extra"]["b"] == 2


class TestDefaultLoggerLogMethods:
    @pytest.fixture
    def logger_and_mock(self):
        name = "test_log_methods"
        dl = DefaultLogger(name=name, min_level=LogLevel.DEBUG)
        mock_logger = MagicMock()
        dl.logger = mock_logger
        return dl, mock_logger

    def test_debug_calls_underlying(self, logger_and_mock):
        dl, mock = logger_and_mock
        dl.debug("msg %s", "arg")
        mock.debug.assert_called_once()
        assert mock.debug.call_args[0][0] == "msg %s"

    def test_info_calls_underlying(self, logger_and_mock):
        dl, mock = logger_and_mock
        dl.info("info msg")
        mock.info.assert_called_once()

    def test_warning_calls_underlying(self, logger_and_mock):
        dl, mock = logger_and_mock
        dl.warning("warn msg")
        mock.warning.assert_called_once()

    def test_error_calls_underlying(self, logger_and_mock):
        dl, mock = logger_and_mock
        dl.error("err msg")
        mock.error.assert_called_once()

    def test_fatal_calls_underlying(self, logger_and_mock):
        dl, mock = logger_and_mock
        dl.fatal("fatal msg")
        mock.critical.assert_called_once()


class TestDefaultLoggerLevelFiltering:
    @pytest.fixture
    def high_level_logger(self):
        dl = DefaultLogger(name="test_filter_level", min_level=LogLevel.ERROR)
        mock_logger = MagicMock()
        dl.logger = mock_logger
        return dl, mock_logger

    def test_debug_filtered_by_high_level(self, high_level_logger):
        dl, mock = high_level_logger
        dl.debug("should not log")
        mock.debug.assert_not_called()

    def test_info_filtered_by_high_level(self, high_level_logger):
        dl, mock = high_level_logger
        dl.info("should not log")
        mock.info.assert_not_called()

    def test_warning_filtered_by_high_level(self, high_level_logger):
        dl, mock = high_level_logger
        dl.warning("should not log")
        mock.warning.assert_not_called()

    def test_error_passes_at_error_level(self, high_level_logger):
        dl, mock = high_level_logger
        dl.error("should log")
        mock.error.assert_called_once()

    def test_fatal_passes_at_error_level(self, high_level_logger):
        dl, mock = high_level_logger
        dl.fatal("should log")
        mock.critical.assert_called_once()


class TestDefaultLoggerSetLevel:
    def test_set_level_updates_min_level(self):
        dl = DefaultLogger(name="test_setlevel1", min_level=LogLevel.INFO)
        dl.set_level(LogLevel.DEBUG)
        assert dl.min_level == LogLevel.DEBUG

    def test_set_level_updates_python_logger(self):
        dl = DefaultLogger(name="test_setlevel2", min_level=LogLevel.INFO)
        dl.set_level(LogLevel.WARNING)
        assert dl.logger.level == logging.WARNING


class TestDefaultLoggerWithFields:
    def test_returns_new_instance(self):
        original = DefaultLogger(name="test_wf1")
        new_logger = original.with_fields(key="value")
        assert new_logger is not original

    def test_new_instance_has_field(self):
        original = DefaultLogger(name="test_wf2")
        new_logger = original.with_fields(key="value")
        assert new_logger.extra_fields["key"] == "value"

    def test_preserves_existing_fields(self):
        original = DefaultLogger(name="test_wf3", extra_fields={"a": 1})
        new_logger = original.with_fields(b=2)
        assert new_logger.extra_fields == {"a": 1, "b": 2}

    def test_original_unmodified(self):
        original = DefaultLogger(name="test_wf4", extra_fields={"a": 1})
        original.with_fields(b=2)
        assert original.extra_fields == {"a": 1}

    def test_overrides_existing_field(self):
        original = DefaultLogger(name="test_wf5", extra_fields={"a": 1})
        new_logger = original.with_fields(a=99)
        assert new_logger.extra_fields["a"] == 99
        assert original.extra_fields["a"] == 1

    def test_preserves_name_and_level(self):
        original = DefaultLogger(name="test_wf6", min_level=LogLevel.WARNING)
        new_logger = original.with_fields(x=1)
        assert new_logger.name == original.name
        assert new_logger.min_level == original.min_level

    def test_isinstance_default_logger(self):
        original = DefaultLogger(name="test_wf7")
        new_logger = original.with_fields(x=1)
        assert isinstance(new_logger, DefaultLogger)

    def test_isinstance_base_logger(self):
        original = DefaultLogger(name="test_wf8")
        new_logger = original.with_fields(x=1)
        assert isinstance(new_logger, BaseLogger)


class TestDefaultLoggerStacklevel:
    def test_stacklevel_passed_through(self):
        dl = DefaultLogger(name="test_stacklevel", min_level=LogLevel.DEBUG)
        mock_logger = MagicMock()
        dl.logger = mock_logger
        dl.info("msg", stacklevel=5)
        _, kwargs = mock_logger.info.call_args
        assert kwargs["stacklevel"] == 5

    def test_stacklevel_defaults_to_one(self):
        dl = DefaultLogger(name="test_stacklevel_default", min_level=LogLevel.DEBUG)
        mock_logger = MagicMock()
        dl.logger = mock_logger
        dl.info("msg")
        _, kwargs = mock_logger.info.call_args
        assert kwargs["stacklevel"] == 1
