"""Unit tests for trpc_agent_sdk.server.openclaw.metrics._metrics."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.server.openclaw.metrics._metrics import (
    _metrics_setup_functions,
    register_metrics,
    setup_metrics,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Snapshot and restore the global registry around each test."""
    original = dict(_metrics_setup_functions)
    yield
    _metrics_setup_functions.clear()
    _metrics_setup_functions.update(original)


class TestRegisterMetrics:

    def test_new_type(self):
        fn = MagicMock()
        result = register_metrics("custom", fn)
        assert result is True
        assert _metrics_setup_functions["custom"] is fn

    def test_existing_type_without_force(self):
        fn_old = MagicMock()
        fn_new = MagicMock()
        _metrics_setup_functions["existing"] = fn_old
        result = register_metrics("existing", fn_new)
        assert result is False
        assert _metrics_setup_functions["existing"] is fn_old

    def test_existing_type_with_force(self):
        fn_old = MagicMock()
        fn_new = MagicMock()
        _metrics_setup_functions["existing"] = fn_old
        result = register_metrics("existing", fn_new, force=True)
        assert result is True
        assert _metrics_setup_functions["existing"] is fn_new

    def test_langfuse_already_registered(self):
        fn = MagicMock()
        result = register_metrics("langfuse", fn, force=False)
        assert result is False

    def test_langfuse_force_override(self):
        fn = MagicMock()
        result = register_metrics("langfuse", fn, force=True)
        assert result is True
        assert _metrics_setup_functions["langfuse"] is fn


class TestSetupMetrics:

    def test_valid_type(self):
        fn = MagicMock(return_value=True)
        _metrics_setup_functions["test_type"] = fn

        config = MagicMock()
        config.metrics.type = "test_type"

        result = setup_metrics(config)
        assert result is True
        fn.assert_called_once_with(config)

    def test_invalid_type(self):
        config = MagicMock()
        config.metrics.type = "nonexistent_type"

        result = setup_metrics(config)
        assert result is False

    def test_setup_function_returns_false(self):
        fn = MagicMock(return_value=False)
        _metrics_setup_functions["fail_type"] = fn

        config = MagicMock()
        config.metrics.type = "fail_type"

        result = setup_metrics(config)
        assert result is False

    def test_exception_handling(self):
        fn = MagicMock(side_effect=RuntimeError("setup boom"))
        _metrics_setup_functions["error_type"] = fn

        config = MagicMock()
        config.metrics.type = "error_type"

        result = setup_metrics(config)
        assert result is False

    def test_exception_in_setup_function_returns_false(self):
        fn = MagicMock(side_effect=Exception("unexpected"))
        _metrics_setup_functions["broken"] = fn

        config = MagicMock()
        config.metrics.type = "broken"

        result = setup_metrics(config)
        assert result is False
