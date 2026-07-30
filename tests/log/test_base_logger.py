# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for _base_logger module (LogLevel and BaseLogger)."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.log._base_logger import BaseLogger, LogLevel


# ---------------------------------------------------------------------------
# LogLevel
# ---------------------------------------------------------------------------


class TestLogLevel:
    def test_trace_value(self):
        assert LogLevel.TRACE.value == 1

    def test_debug_value(self):
        assert LogLevel.DEBUG.value == 2

    def test_info_value(self):
        assert LogLevel.INFO.value == 3

    def test_warning_value(self):
        assert LogLevel.WARNING.value == 4

    def test_error_value(self):
        assert LogLevel.ERROR.value == 5

    def test_fatal_value(self):
        assert LogLevel.FATAL.value == 6

    def test_all_members_present(self):
        expected = {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "FATAL"}
        assert set(LogLevel.__members__.keys()) == expected

    def test_values_are_unique(self):
        values = [member.value for member in LogLevel]
        assert len(values) == len(set(values))

    def test_ordering(self):
        levels = list(LogLevel)
        for i in range(len(levels) - 1):
            assert levels[i].value < levels[i + 1].value

    def test_member_count(self):
        assert len(LogLevel) == 6


# ---------------------------------------------------------------------------
# Concrete subclass for testing BaseLogger
# ---------------------------------------------------------------------------


class StubLogger(BaseLogger):
    """Minimal concrete logger for testing the abstract base class."""

    def __init__(self, name: str = "stub", min_level: LogLevel = LogLevel.DEBUG):
        super().__init__(name, min_level)
        self.calls: list[tuple[str, str, tuple, dict]] = []

    def debug(self, format_str: str, *args, **kwargs):
        self.calls.append(("debug", format_str, args, kwargs))

    def info(self, format_str: str, *args, **kwargs):
        self.calls.append(("info", format_str, args, kwargs))

    def warning(self, format_str: str, *args, **kwargs):
        self.calls.append(("warning", format_str, args, kwargs))

    def error(self, format_str: str, *args, **kwargs):
        self.calls.append(("error", format_str, args, kwargs))

    def fatal(self, format_str: str, *args, **kwargs):
        self.calls.append(("fatal", format_str, args, kwargs))


# ---------------------------------------------------------------------------
# BaseLogger
# ---------------------------------------------------------------------------


class TestBaseLoggerInit:
    def test_default_name(self):
        logger = StubLogger()
        assert logger.name == "stub"

    def test_default_min_level(self):
        logger = StubLogger()
        assert logger.min_level == LogLevel.DEBUG

    def test_custom_name(self):
        logger = StubLogger(name="custom")
        assert logger.name == "custom"

    def test_custom_min_level(self):
        logger = StubLogger(min_level=LogLevel.ERROR)
        assert logger.min_level == LogLevel.ERROR


class TestBaseLoggerSetLevel:
    def test_set_level_changes_min_level(self):
        logger = StubLogger()
        logger.set_level(LogLevel.WARNING)
        assert logger.min_level == LogLevel.WARNING

    def test_set_level_to_trace(self):
        logger = StubLogger(min_level=LogLevel.FATAL)
        logger.set_level(LogLevel.TRACE)
        assert logger.min_level == LogLevel.TRACE


class TestBaseLoggerWithFields:
    def test_returns_self(self):
        logger = StubLogger()
        result = logger.with_fields(key="value")
        assert result is logger

    def test_returns_base_logger_type(self):
        logger = StubLogger()
        result = logger.with_fields(a=1, b=2)
        assert isinstance(result, BaseLogger)


class TestBaseLoggerAbstractMethods:
    def test_cannot_instantiate_without_implementations(self):
        with pytest.raises(TypeError):
            BaseLogger()  # type: ignore[abstract]

    def test_stub_logger_debug(self):
        logger = StubLogger()
        logger.debug("msg %s", "a", extra={"k": "v"})
        assert logger.calls[-1] == ("debug", "msg %s", ("a",), {"extra": {"k": "v"}})

    def test_stub_logger_info(self):
        logger = StubLogger()
        logger.info("hello")
        assert logger.calls[-1][0] == "info"

    def test_stub_logger_warning(self):
        logger = StubLogger()
        logger.warning("warn")
        assert logger.calls[-1][0] == "warning"

    def test_stub_logger_error(self):
        logger = StubLogger()
        logger.error("err")
        assert logger.calls[-1][0] == "error"

    def test_stub_logger_fatal(self):
        logger = StubLogger()
        logger.fatal("fatal")
        assert logger.calls[-1][0] == "fatal"
