# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Model retry configuration for TRPC Agent framework."""

from __future__ import annotations

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class ExponentialBackoffConfig(BaseModel):
    """Configuration for exponential retry backoff."""

    model_config = ConfigDict(extra="forbid")

    initial_backoff: float = Field(default=1.0, ge=0.0)
    """Base backoff in seconds for the first exponential retry."""

    max_backoff: float = Field(default=10.0, ge=0.0)
    """Upper bound in seconds for any single computed backoff."""

    multiplier: float = Field(default=2.0, ge=1.0)
    """Exponential growth factor per attempt."""

    jitter: bool = True
    """Whether to apply full jitter to computed backoff values."""


class ModelRetryConfig(BaseModel):
    """SDK-managed model retry configuration."""

    model_config = ConfigDict(extra="forbid")

    num_retries: int = Field(default=2, ge=0)
    """Retry attempts in addition to the initial call."""

    backoff: ExponentialBackoffConfig = Field(default_factory=ExponentialBackoffConfig)
    """Exponential backoff configuration used between retries."""
