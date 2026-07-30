# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Loaded-skill order helpers.
"""

from __future__ import annotations

import json
from typing import Any


def parse_loaded_order(raw: Any) -> list[str]:
    """Parse a stored loaded-order payload.

    Returns a normalized list of unique non-empty skill names.
    """
    if raw is None:
        return []
    if isinstance(raw, bytes):
        if not raw:
            return []
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return []
    if isinstance(raw, str):
        if not raw:
            return []
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return _normalize_loaded_order(raw)


def marshal_loaded_order(names: list[str]) -> str:
    """Serialize a normalized loaded-order payload."""
    normalized = _normalize_loaded_order(names)
    if not normalized:
        return ""
    return json.dumps(normalized, ensure_ascii=False)


def touch_loaded_order(names: list[str], *touched: str) -> list[str]:
    """Move touched skills to the tail of the loaded order."""
    order = _normalize_loaded_order(names)
    for name in touched:
        candidate = (name or "").strip()
        if not candidate:
            continue
        order = _remove_loaded_order_name(order, candidate)
        order.append(candidate)
    return order


def _normalize_loaded_order(names: list[Any]) -> list[str]:
    if not names:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str):
            continue
        candidate = name.strip()
        if not candidate or candidate in seen:
            continue
        out.append(candidate)
        seen.add(candidate)
    return out


def _remove_loaded_order_name(order: list[str], target: str) -> list[str]:
    for i, name in enumerate(order):
        if name != target:
            continue
        return order[:i] + order[i + 1:]
    return order
