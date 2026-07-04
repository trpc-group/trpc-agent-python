# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Normalizer for cross-backend comparison of sessions, events, and summaries.

Normalization strategies:
- Timestamp: round to millisecond precision to account for float vs DB precision.
- Auto-generated IDs: replace with placeholder {AUTO_ID} for event_id, invocation_id.
- JSON key ordering: sort dict keys to eliminate serialization order differences.
- Null/empty: normalize None, "", [], {} to a single sentinel.
- Summary text: strip extra whitespace, normalize punctuation.
"""

from __future__ import annotations

import re
from typing import Any
from typing import Optional


TIMESTAMP_PRECISION = 3


class Normalizer:
    """Normalizes backend snapshots for fair cross-backend comparison."""

    def normalize_timestamp(self, ts: Optional[float]) -> float:
        """Round timestamp to uniform precision.

        Returns 0.0 for None to handle backends that may store NULL timestamps.
        """
        if ts is None:
            return 0.0
        return round(ts, TIMESTAMP_PRECISION)

    def normalize_id(self, value: str) -> str:
        """Replace auto-generated IDs with a placeholder."""
        if not value:
            return value
        return "{AUTO_ID}"

    def normalize_null(self, value: Any) -> Any:
        """Normalize various empty representations to None."""
        if value is None:
            return None
        if isinstance(value, str) and value == "":
            return None
        if isinstance(value, (list, dict)) and len(value) == 0:
            return None
        return value

    def normalize_json_keys(self, d: dict[str, Any]) -> dict[str, Any]:
        """Sort dict keys alphabetically to eliminate serialization order diffs."""
        return dict(sorted(d.items()))

    def normalize_summary_text(self, text: str) -> str:
        """Normalize summary text for semantic comparison.

        Strips extra whitespace and normalizes common punctuation variants.
        """
        if not text:
            return ""
        text = re.sub(r'\s+', ' ', text).strip()
        text = text.replace("\u3010", "").replace("\u3011", "")
        text = text.replace("\uff0c", ",").replace("\uff0e", ".")
        text = text.replace("\uff01", "!").replace("\uff1f", "?")
        text = text.replace("\uff1a", ":").replace("\uff1b", ";")
        return text

    def normalize_event(self, event_dict: dict[str, Any]) -> dict[str, Any]:
        """Normalize a single event dict for comparison.

        Replaces auto-generated IDs and normalizes timestamps.
        """
        normalized = dict(event_dict)
        if "id" in normalized:
            normalized["id"] = self.normalize_id(normalized["id"])
        if "invocation_id" in normalized:
            normalized["invocation_id"] = self.normalize_id(normalized["invocation_id"])
        if "timestamp" in normalized:
            normalized["timestamp"] = self.normalize_timestamp(normalized["timestamp"])
        if "request_id" in normalized and normalized["request_id"]:
            normalized["request_id"] = self.normalize_id(normalized["request_id"])
        if "parent_invocation_id" in normalized and normalized["parent_invocation_id"]:
            normalized["parent_invocation_id"] = self.normalize_id(
                normalized["parent_invocation_id"]
            )
        self._normalize_scalar_fields(normalized)
        return normalized

    def normalize_session(self, session_dict: dict[str, Any]) -> dict[str, Any]:
        """Normalize a session dict for comparison."""
        normalized = dict(session_dict)
        if "last_update_time" in normalized:
            normalized["last_update_time"] = self.normalize_timestamp(
                normalized["last_update_time"]
            )
        if "events" in normalized:
            normalized["events"] = [
                self.normalize_event(e) for e in normalized["events"]
            ]
        self._normalize_scalar_fields(normalized)
        return normalized

    def normalize_summary(self, summary_dict: dict[str, Any]) -> dict[str, Any]:
        """Normalize a summary dict for comparison."""
        normalized = dict(summary_dict)
        if "summary_text" in normalized:
            normalized["summary_text"] = self.normalize_summary_text(
                normalized["summary_text"]
            )
        if "summary_timestamp" in normalized:
            normalized["summary_timestamp"] = self.normalize_timestamp(
                normalized["summary_timestamp"]
            )
        self._normalize_scalar_fields(normalized)
        return normalized

    def _normalize_scalar_fields(self, d: dict[str, Any]) -> None:
        """Normalize null/empty representations for all scalar fields.

        Skips lists and dicts to avoid destroying structured data like events
        or state, but normalizes None, "", [], {} for all other types so that
        different backends' empty representations are treated as equal.
        """
        for key, value in d.items():
            if not isinstance(value, (list, dict)):
                d[key] = self.normalize_null(value)