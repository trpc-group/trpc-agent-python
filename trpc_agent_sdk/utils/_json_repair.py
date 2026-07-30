# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""JSON parsing helpers with json-repair fallback."""

from __future__ import annotations

import json
from typing import Any

import json_repair


def json_repair_string(value: str | bytes | bytearray, **kwargs: Any) -> str:
    """Return a valid JSON string, repairing malformed JSON when needed.

    Use this when the downstream API expects JSON text, such as
    ``BaseModel.model_validate_json`` or when a repaired JSON string needs to
    be logged/transmitted.
    """
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")

    try:
        return json_repair.repair_json(value, **kwargs)
    except Exception as exc:  # pylint: disable=broad-except
        raise json.JSONDecodeError(str(exc), value, 0) from exc


def json_loads_repair(value: str | bytes | bytearray, **kwargs: Any) -> Any:
    """Load JSON, falling back to ``json_repair`` for malformed LLM JSON.

    This should be used only on text that may come from model/provider output
    or tool-call arguments. Persisted data/config paths should keep strict
    ``json.loads`` so storage corruption remains visible.
    """
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")

    try:
        return json_repair.loads(value, **kwargs)
    except Exception as exc:  # pylint: disable=broad-except
        raise json.JSONDecodeError(str(exc), value, 0) from exc
