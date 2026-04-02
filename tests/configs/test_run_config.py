# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for RunConfig (_run_config).

Covers:
- Default field values
- Custom field assignment
- Pydantic extra="forbid" enforcement
- max_llm_calls validator (sys.maxsize rejection, <=0 warning)
- Field types and factory defaults
- Public re-export from trpc_agent_sdk.configs
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.configs._run_config import RunConfig as RunConfigDirect


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------


class TestRunConfigDefaults:
    """All fields should have sensible defaults when no args are passed."""

    def test_default_max_llm_calls(self):
        cfg = RunConfig()
        assert cfg.max_llm_calls == 500

    def test_default_streaming(self):
        cfg = RunConfig()
        assert cfg.streaming is True

    def test_default_agent_run_config(self):
        cfg = RunConfig()
        assert cfg.agent_run_config == {}

    def test_default_agent_run_config_is_new_instance(self):
        cfg1 = RunConfig()
        cfg2 = RunConfig()
        assert cfg1.agent_run_config is not cfg2.agent_run_config

    def test_default_custom_data(self):
        cfg = RunConfig()
        assert cfg.custom_data == {}

    def test_default_custom_data_is_new_instance(self):
        cfg1 = RunConfig()
        cfg2 = RunConfig()
        assert cfg1.custom_data is not cfg2.custom_data

    def test_default_save_history_enabled(self):
        cfg = RunConfig()
        assert cfg.save_history_enabled is False

    def test_default_start_from_last_agent(self):
        cfg = RunConfig()
        assert cfg.start_from_last_agent is False


# ---------------------------------------------------------------------------
# Custom values
# ---------------------------------------------------------------------------


class TestRunConfigCustomValues:
    """Fields accept non-default values."""

    def test_custom_max_llm_calls(self):
        cfg = RunConfig(max_llm_calls=100)
        assert cfg.max_llm_calls == 100

    def test_custom_streaming_false(self):
        cfg = RunConfig(streaming=False)
        assert cfg.streaming is False

    def test_custom_agent_run_config(self):
        data = {"timeout": 30, "retries": 3}
        cfg = RunConfig(agent_run_config=data)
        assert cfg.agent_run_config == data

    def test_custom_custom_data(self):
        data = {"api_key": "secret", "region": "us-east-1"}
        cfg = RunConfig(custom_data=data)
        assert cfg.custom_data == data

    def test_custom_save_history_enabled(self):
        cfg = RunConfig(save_history_enabled=True)
        assert cfg.save_history_enabled is True

    def test_custom_start_from_last_agent(self):
        cfg = RunConfig(start_from_last_agent=True)
        assert cfg.start_from_last_agent is True

    def test_all_fields_together(self):
        cfg = RunConfig(
            max_llm_calls=200,
            streaming=False,
            agent_run_config={"k": "v"},
            custom_data={"key": 42},
            save_history_enabled=True,
            start_from_last_agent=True,
        )
        assert cfg.max_llm_calls == 200
        assert cfg.streaming is False
        assert cfg.agent_run_config == {"k": "v"}
        assert cfg.custom_data == {"key": 42}
        assert cfg.save_history_enabled is True
        assert cfg.start_from_last_agent is True


# ---------------------------------------------------------------------------
# extra="forbid" enforcement
# ---------------------------------------------------------------------------


class TestRunConfigExtraForbid:
    """Pydantic ConfigDict(extra='forbid') rejects unknown fields."""

    def test_unknown_field_raises_validation_error(self):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            RunConfig(unknown_field="oops")

    def test_multiple_unknown_fields_rejected(self):
        with pytest.raises(ValidationError):
            RunConfig(foo=1, bar=2)

    def test_known_plus_unknown_rejected(self):
        with pytest.raises(ValidationError):
            RunConfig(max_llm_calls=10, nonexistent="x")


# ---------------------------------------------------------------------------
# max_llm_calls validator
# ---------------------------------------------------------------------------


class TestMaxLlmCallsValidator:
    """Tests for validate_max_llm_calls field_validator."""

    def test_sys_maxsize_raises_value_error(self):
        with pytest.raises(ValidationError, match=f"less than {sys.maxsize}"):
            RunConfig(max_llm_calls=sys.maxsize)

    def test_just_below_sys_maxsize_ok(self):
        cfg = RunConfig(max_llm_calls=sys.maxsize - 1)
        assert cfg.max_llm_calls == sys.maxsize - 1

    def test_positive_value_accepted(self):
        cfg = RunConfig(max_llm_calls=1)
        assert cfg.max_llm_calls == 1

    def test_large_positive_value_accepted(self):
        cfg = RunConfig(max_llm_calls=999999)
        assert cfg.max_llm_calls == 999999

    def test_zero_logs_warning(self):
        with patch("trpc_agent_sdk.configs._run_config.logger") as mock_logger:
            cfg = RunConfig(max_llm_calls=0)
            assert cfg.max_llm_calls == 0
            mock_logger.warning.assert_called_once()
            assert "less than or equal to 0" in mock_logger.warning.call_args[0][0]

    def test_negative_value_logs_warning(self):
        with patch("trpc_agent_sdk.configs._run_config.logger") as mock_logger:
            cfg = RunConfig(max_llm_calls=-1)
            assert cfg.max_llm_calls == -1
            mock_logger.warning.assert_called_once()

    def test_large_negative_value_logs_warning(self):
        with patch("trpc_agent_sdk.configs._run_config.logger") as mock_logger:
            cfg = RunConfig(max_llm_calls=-1000)
            assert cfg.max_llm_calls == -1000
            mock_logger.warning.assert_called_once()

    def test_positive_value_no_warning(self):
        with patch("trpc_agent_sdk.configs._run_config.logger") as mock_logger:
            RunConfig(max_llm_calls=10)
            mock_logger.warning.assert_not_called()

    def test_default_value_no_warning(self):
        with patch("trpc_agent_sdk.configs._run_config.logger") as mock_logger:
            RunConfig()
            mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# Type coercion / validation
# ---------------------------------------------------------------------------


class TestRunConfigTypeValidation:
    """Pydantic type coercion and validation behavior."""

    def test_max_llm_calls_coerces_from_string_like(self):
        cfg = RunConfig(max_llm_calls=50)
        assert isinstance(cfg.max_llm_calls, int)

    def test_invalid_max_llm_calls_type_raises(self):
        with pytest.raises(ValidationError):
            RunConfig(max_llm_calls="not_a_number")

    def test_invalid_streaming_type_raises(self):
        with pytest.raises(ValidationError):
            RunConfig(streaming="not_a_bool")

    def test_invalid_agent_run_config_type_raises(self):
        with pytest.raises(ValidationError):
            RunConfig(agent_run_config="not_a_dict")

    def test_invalid_custom_data_type_raises(self):
        with pytest.raises(ValidationError):
            RunConfig(custom_data="not_a_dict")

    def test_invalid_save_history_type_raises(self):
        with pytest.raises(ValidationError):
            RunConfig(save_history_enabled="not_a_bool")

    def test_invalid_start_from_last_agent_type_raises(self):
        with pytest.raises(ValidationError):
            RunConfig(start_from_last_agent="not_a_bool")

    def test_nested_dict_values_preserved(self):
        nested = {"outer": {"inner": [1, 2, 3]}}
        cfg = RunConfig(agent_run_config=nested)
        assert cfg.agent_run_config["outer"]["inner"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Public re-export
# ---------------------------------------------------------------------------


class TestPublicExport:
    """RunConfig should be importable from trpc_agent_sdk.configs."""

    def test_reexport_is_same_class(self):
        assert RunConfig is RunConfigDirect

    def test_importable_from_package(self):
        from trpc_agent_sdk.configs import RunConfig as RC
        assert RC is RunConfig


# ---------------------------------------------------------------------------
# Pydantic model behavior
# ---------------------------------------------------------------------------


class TestPydanticModelBehavior:
    """Tests for general Pydantic model behaviors on RunConfig."""

    def test_model_dump_returns_all_fields(self):
        cfg = RunConfig()
        d = cfg.model_dump()
        expected_keys = {
            "max_llm_calls",
            "streaming",
            "agent_run_config",
            "custom_data",
            "save_history_enabled",
            "start_from_last_agent",
        }
        assert set(d.keys()) == expected_keys

    def test_model_dump_reflects_custom_values(self):
        cfg = RunConfig(max_llm_calls=42, streaming=False)
        d = cfg.model_dump()
        assert d["max_llm_calls"] == 42
        assert d["streaming"] is False

    def test_model_json_schema_has_all_fields(self):
        schema = RunConfig.model_json_schema()
        props = schema.get("properties", {})
        assert "max_llm_calls" in props
        assert "streaming" in props
        assert "agent_run_config" in props
        assert "custom_data" in props
        assert "save_history_enabled" in props
        assert "start_from_last_agent" in props

    def test_model_copy_creates_independent_instance(self):
        cfg1 = RunConfig(agent_run_config={"a": 1})
        cfg2 = cfg1.model_copy(deep=True)
        cfg2.agent_run_config["b"] = 2
        assert "b" not in cfg1.agent_run_config

    def test_construct_from_dict(self):
        data = {"max_llm_calls": 300, "streaming": False}
        cfg = RunConfig(**data)
        assert cfg.max_llm_calls == 300
        assert cfg.streaming is False

    def test_equality_of_same_config(self):
        cfg1 = RunConfig(max_llm_calls=100)
        cfg2 = RunConfig(max_llm_calls=100)
        assert cfg1 == cfg2

    def test_inequality_of_different_config(self):
        cfg1 = RunConfig(max_llm_calls=100)
        cfg2 = RunConfig(max_llm_calls=200)
        assert cfg1 != cfg2
