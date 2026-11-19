# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Common types for the cancel and steering control plane."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionKey:
    """Immutable key that identifies the active run of a session."""

    app_name: str
    user_id: str
    session_id: str


@dataclass(frozen=True)
class Status:
    """Result of a cancel or steering request.

    ``ok`` reports whether the request was accepted. ``error_msg`` is free-form
    detail supplied by the manager when ``ok`` is ``False``. The object is
    truthy when ``ok`` is true, so callers may use it directly in boolean
    checks.
    """

    ok: bool
    error_msg: str = ""

    def __bool__(self) -> bool:
        return self.ok


__all__ = [
    "SessionKey",
    "Status",
]
