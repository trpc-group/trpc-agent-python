# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Data models for replay consistency tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReplayCase:
    """JSONL-driven replay case for deterministic backend comparison."""

    name: str
    app_name: str
    user_id: str
    session_id: str
    session_config: dict[str, Any]
    memory_config: dict[str, Any]
    summary_points: list[int]
    event_records: list[dict[str, Any]]
    memory_search_records: list[dict[str, Any]]
