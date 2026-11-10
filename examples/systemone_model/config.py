# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Read System One client settings from the environment."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class TypeSafeSettings:
    """Settings for ``SystemOneClient.typesafe``."""

    api_key: str
    base_url: str | None
    default_model: str | None


@dataclass(frozen=True)
class LayaSettings:
    """Settings for ``SystemOneClient.laya``."""

    base_url: str
    api_key: str | None
    default_model: str | None


def typesafe_settings() -> TypeSafeSettings | None:
    """Return TypeSafe settings, or ``None`` when ``TYPESAFE_API_KEY`` is empty."""
    api_key = os.getenv("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        return None
    return TypeSafeSettings(
        api_key=api_key,
        base_url=_optional("TYPESAFE_BASE_URL"),
        default_model=_optional("TYPESAFE_DEFAULT_MODEL"),
    )


def laya_settings() -> LayaSettings | None:
    """Return Laya settings, or ``None`` when ``LAYA_BASE_URL`` is empty."""
    base_url = os.getenv("LAYA_BASE_URL", "").strip()
    if not base_url:
        return None
    return LayaSettings(
        base_url=base_url,
        api_key=_optional("LAYA_API_KEY"),
        default_model=_optional("LAYA_MODEL"),
    )


def _optional(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None
