# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for ModelRetryConfig (_model_retry_config)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trpc_agent_sdk.configs import ExponentialBackoffConfig
from trpc_agent_sdk.configs import ModelRetryConfig
from trpc_agent_sdk.configs._model_retry_config import ExponentialBackoffConfig as ExponentialBackoffConfigDirect
from trpc_agent_sdk.configs._model_retry_config import ModelRetryConfig as ModelRetryConfigDirect


class TestDefaults:
    def test_default_values(self):
        cfg = ModelRetryConfig()
        assert cfg.num_retries == 2
        assert isinstance(cfg.backoff, ExponentialBackoffConfig)
        assert cfg.backoff.initial_backoff == 1.0
        assert cfg.backoff.max_backoff == 10.0
        assert cfg.backoff.multiplier == 2.0
        assert cfg.backoff.jitter is True


class TestValidation:
    def test_negative_num_retries_rejected(self):
        with pytest.raises(ValidationError):
            ModelRetryConfig(num_retries=-1)

    def test_zero_num_retries_allowed(self):
        cfg = ModelRetryConfig(num_retries=0)
        assert cfg.num_retries == 0

    def test_multiplier_below_one_rejected(self):
        with pytest.raises(ValidationError):
            ExponentialBackoffConfig(multiplier=0.5)

    def test_negative_exponential_backoff_rejected(self):
        with pytest.raises(ValidationError):
            ExponentialBackoffConfig(initial_backoff=-1.0)

    def test_fixed_backoff_shape_rejected(self):
        with pytest.raises(ValidationError):
            ModelRetryConfig(backoff={"type": "fixed", "interval": 1.0})

    def test_unknown_backoff_shape_rejected(self):
        with pytest.raises(ValidationError):
            ModelRetryConfig(backoff={"type": "linear", "step": 0.5})

    def test_old_flat_fields_are_rejected(self):
        with pytest.raises(ValidationError):
            ModelRetryConfig(initial_backoff=1.0)
        with pytest.raises(ValidationError):
            ModelRetryConfig(backoff_strategy="fixed")
        with pytest.raises(ValidationError):
            ModelRetryConfig(retryable_error_codes=["429"])
        with pytest.raises(ValidationError):
            ModelRetryConfig(rules={"retryable_error_codes": ["429"]})


class TestExport:
    def test_reexports_are_same_classes(self):
        assert ModelRetryConfig is ModelRetryConfigDirect
        assert ExponentialBackoffConfig is ExponentialBackoffConfigDirect
