# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for _logger module (global logger functions and registry)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.log._base_logger import BaseLogger, LogLevel
from trpc_agent_sdk.log._default_logger import DefaultLogger, RelativePathFormatter
from trpc_agent_sdk.log import _logger as logger_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class StubLogger(BaseLogger):
    """Minimal logger for testing global functions."""

    def __init__(self, name="stub", min_level=LogLevel.DEBUG):
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

    def with_fields(self, **kwargs) -> StubLogger:
        new = StubLogger(self.name, self.min_level)
        return new


class FailingLogger(BaseLogger):
    """Logger that raises on every call, used to test error fallback paths."""

    def debug(self, format_str: str, *args, **kwargs):
        raise RuntimeError("debug fail")

    def info(self, format_str: str, *args, **kwargs):
        raise RuntimeError("info fail")

    def warning(self, format_str: str, *args, **kwargs):
        raise RuntimeError("warning fail")

    def error(self, format_str: str, *args, **kwargs):
        raise RuntimeError("error fail")

    def fatal(self, format_str: str, *args, **kwargs):
        raise RuntimeError("fatal fail")

    def with_fields(self, **kwargs):
        return self


@pytest.fixture(autouse=True)
def _restore_global_state():
    """Save and restore global logger state around each test."""
    orig_loggers = logger_module._loggers.copy()
    orig_current = logger_module._current_logger
    orig_default_name = logger_module._default_logger_name
    yield
    logger_module._loggers = orig_loggers
    logger_module._current_logger = orig_current
    logger_module._default_logger_name = orig_default_name


# ---------------------------------------------------------------------------
# Global state defaults
# ---------------------------------------------------------------------------


class TestModuleDefaults:
    def test_default_logger_is_default_logger_instance(self):
        assert isinstance(logger_module._current_logger, DefaultLogger)

    def test_loggers_dict_has_default(self):
        assert "default" in logger_module._loggers

    def test_default_logger_name(self):
        assert logger_module._default_logger_name == "default"


# ---------------------------------------------------------------------------
# get_logger
# ---------------------------------------------------------------------------


class TestGetLogger:
    def test_no_name_returns_current(self):
        stub = StubLogger()
        logger_module._current_logger = stub
        assert logger_module.get_logger() is stub

    def test_known_name_returns_registered(self):
        stub = StubLogger(name="named")
        logger_module._loggers["named"] = stub
        assert logger_module.get_logger("named") is stub

    def test_unknown_name_returns_current_and_warns(self):
        stub = StubLogger(name="current")
        logger_module._current_logger = stub
        result = logger_module.get_logger("nonexistent")
        assert result is stub
        has_warning = any(c[0] == "warning" for c in stub.calls)
        assert has_warning

    def test_none_name_returns_current(self):
        result = logger_module.get_logger(None)
        assert result is logger_module._current_logger


# ---------------------------------------------------------------------------
# set_logger
# ---------------------------------------------------------------------------


class TestSetLogger:
    def test_set_without_name_updates_current(self):
        stub = StubLogger()
        logger_module.set_logger(stub)
        assert logger_module._current_logger is stub

    def test_set_without_name_registers_under_default(self):
        stub = StubLogger()
        logger_module.set_logger(stub)
        assert logger_module._loggers["default"] is stub

    def test_set_with_name_registers(self):
        stub = StubLogger()
        logger_module.set_logger(stub, name="custom")
        assert logger_module._loggers["custom"] is stub

    def test_set_with_name_does_not_change_current(self):
        original_current = logger_module._current_logger
        stub = StubLogger()
        logger_module.set_logger(stub, name="other")
        assert logger_module._current_logger is original_current


# ---------------------------------------------------------------------------
# register_logger
# ---------------------------------------------------------------------------


class TestRegisterLogger:
    def test_register_adds_to_loggers(self):
        stub = StubLogger()
        logger_module.register_logger("reg_test", stub)
        assert logger_module._loggers["reg_test"] is stub

    def test_register_does_not_change_current(self):
        original = logger_module._current_logger
        logger_module.register_logger("reg_test2", StubLogger())
        assert logger_module._current_logger is original


# ---------------------------------------------------------------------------
# Module-level log functions
# ---------------------------------------------------------------------------


class TestDebugFunction:
    def test_delegates_to_current_logger(self):
        stub = StubLogger()
        logger_module._current_logger = stub
        logger_module.debug("hello %s", "world")
        assert len(stub.calls) == 1
        assert stub.calls[0][0] == "debug"
        assert stub.calls[0][1] == "hello %s"
        assert stub.calls[0][2] == ("world",)

    def test_sets_stacklevel(self):
        stub = StubLogger()
        logger_module._current_logger = stub
        logger_module.debug("msg")
        assert stub.calls[0][3]["stacklevel"] == logger_module._STACK_LEVEL

    def test_exception_falls_back_to_error(self):
        failing = FailingLogger()
        stub = StubLogger()
        logger_module._current_logger = failing
        # debug -> raises -> calls error -> raises -> print to stderr
        with patch("builtins.print") as mock_print:
            logger_module.debug("boom")
            mock_print.assert_called_once()


class TestInfoFunction:
    def test_delegates_to_current_logger(self):
        stub = StubLogger()
        logger_module._current_logger = stub
        logger_module.info("info msg")
        assert stub.calls[0][0] == "info"

    def test_exception_falls_back_to_error(self):
        failing = FailingLogger()
        logger_module._current_logger = failing
        with patch("builtins.print") as mock_print:
            logger_module.info("boom")
            mock_print.assert_called_once()


class TestWarningFunction:
    def test_delegates_to_current_logger(self):
        stub = StubLogger()
        logger_module._current_logger = stub
        logger_module.warning("warn msg")
        assert stub.calls[0][0] == "warning"

    def test_exception_falls_back_to_error(self):
        failing = FailingLogger()
        logger_module._current_logger = failing
        with patch("builtins.print") as mock_print:
            logger_module.warning("boom")
            mock_print.assert_called_once()


class TestErrorFunction:
    def test_delegates_to_current_logger(self):
        stub = StubLogger()
        logger_module._current_logger = stub
        logger_module.error("err msg")
        assert stub.calls[0][0] == "error"

    def test_exception_falls_back_to_stderr(self, capsys):
        failing = FailingLogger()
        logger_module._current_logger = failing
        logger_module.error("boom")
        captured = capsys.readouterr()
        assert "log err!" in captured.err


class TestFatalFunction:
    def test_delegates_to_current_logger(self):
        stub = StubLogger()
        logger_module._current_logger = stub
        logger_module.fatal("fatal msg")
        assert stub.calls[0][0] == "fatal"

    def test_exception_falls_back_to_error(self):
        failing = FailingLogger()
        logger_module._current_logger = failing
        with patch("builtins.print") as mock_print:
            logger_module.fatal("boom")
            mock_print.assert_called_once()


# ---------------------------------------------------------------------------
# with_fields
# ---------------------------------------------------------------------------


class TestWithFieldsFunction:
    def test_delegates_to_current_logger(self):
        stub = StubLogger()
        logger_module._current_logger = stub
        result = logger_module.with_fields(key="val")
        assert isinstance(result, StubLogger)

    def test_returns_new_instance(self):
        stub = StubLogger()
        logger_module._current_logger = stub
        result = logger_module.with_fields(k=1)
        assert result is not stub


# ---------------------------------------------------------------------------
# set_default_logger
# ---------------------------------------------------------------------------


class TestSetDefaultLogger:
    def test_sets_current_logger(self):
        stub = StubLogger()
        logger_module.set_default_logger(stub)
        assert logger_module._current_logger is stub

    def test_same_as_set_logger(self):
        stub = StubLogger()
        logger_module.set_default_logger(stub)
        assert logger_module._loggers["default"] is stub


# ---------------------------------------------------------------------------
# __init__.py exports
# ---------------------------------------------------------------------------


class TestInitExports:
    def test_logger_module_alias(self):
        from trpc_agent_sdk.log import logger
        assert logger is logger_module

    def test_base_logger_exported(self):
        from trpc_agent_sdk.log import BaseLogger as BL
        assert BL is BaseLogger

    def test_log_level_exported(self):
        from trpc_agent_sdk.log import LogLevel as LL
        assert LL is LogLevel

    def test_default_logger_exported(self):
        from trpc_agent_sdk.log import DefaultLogger as DL
        assert DL is DefaultLogger

    def test_relative_path_formatter_exported(self):
        from trpc_agent_sdk.log import RelativePathFormatter as RPF
        assert RPF is RelativePathFormatter

    def test_all_functions_exported(self):
        from trpc_agent_sdk import log
        for name in [
            "debug", "error", "fatal", "get_logger",
            "info", "register_logger", "set_default_logger",
            "set_logger", "warning", "with_fields",
        ]:
            assert hasattr(log, name)

    def test_all_list_complete(self):
        from trpc_agent_sdk import log
        expected = {
            "logger", "BaseLogger", "LogLevel", "DefaultLogger",
            "RelativePathFormatter", "debug", "error", "fatal",
            "get_logger", "info", "register_logger",
            "set_default_logger", "set_logger", "warning", "with_fields",
        }
        assert set(log.__all__) == expected
