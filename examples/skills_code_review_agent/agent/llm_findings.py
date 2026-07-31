# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Parsing of structured findings emitted by the LLM in Agent mode.

The Agent is instructed to terminate its review with a ``FINDINGS_JSON`` block
containing a JSON array of findings. This module extracts and normalizes those
findings so they can be merged with the deterministic rule-engine results.

The parser is deliberately permissive (models do not always emit strict JSON):
- it looks for the ``FINDINGS_JSON`` marker first,
- then falls back to the first JSON array found anywhere in the text,
- malformed/out-of-schema entries are dropped rather than raised.
"""

import json
import re
from typing import Any

FINDINGS_MARKER = "FINDINGS_JSON"

_SEVERITY_MAP = {
    'critical': 'critical', 'blocker': 'critical', 'blocker/critical': 'critical',
    'high': 'high', 'major': 'high',
    'medium': 'medium', 'moderate': 'medium', 'warning': 'medium',
    'low': 'low', 'minor': 'low', 'info': 'low', 'informational': 'low',
}

_CATEGORIES = {
    'security', 'resource_leak', 'error_handling', 'testing',
    'database', 'concurrency', 'performance', 'other',
}

_REQUIRED = ('severity', 'category', 'file', 'line', 'title')

_JSON_BLOCK_RE = re.compile(r'FINDINGS_JSON\s*[:=]?\s*(\[.*\])', re.DOTALL)
_ARRAY_RE = re.compile(r'(\[[^\]]*\])', re.DOTALL)


def _normalize_severity(value: Any) -> str:
    if not isinstance(value, str):
        return 'medium'
    return _SEVERITY_MAP.get(value.strip().lower(), 'medium')


def _normalize_category(value: Any) -> str:
    if not isinstance(value, str):
        return 'other'
    key = value.strip().lower().replace(' ', '_').replace('-', '_')
    return key if key in _CATEGORIES else 'other'


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any, default: float = 0.85) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_finding(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if not all(item.get(field) for field in ('title', 'file')):
        return None
    return {
        'severity': _normalize_severity(item.get('severity')),
        'category': _normalize_category(item.get('category')),
        'file': str(item.get('file', '')),
        'line': _as_int(item.get('line')),
        'title': str(item.get('title', '')).strip(),
        'evidence': str(item.get('evidence', '')),
        'recommendation': str(item.get('recommendation', '')),
        'confidence': _as_float(item.get('confidence')),
        'rule_id': str(item.get('rule_id') or f"LLM-{_normalize_category(item.get('category'))}").upper(),
        'source': 'llm',
    }


def _try_parse_json(raw: str) -> list[Any] | None:
    raw = raw.strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, list) else None


def _extract_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    block = _JSON_BLOCK_RE.search(text)
    if block:
        candidates.append(block.group(1))
    for m in _ARRAY_RE.finditer(text):
        if m.group(1) != (block.group(1) if block else None):
            candidates.append(m.group(1))
    return candidates


def parse_llm_findings(text: str) -> list[dict[str, Any]]:
    """Parse and normalize LLM-emitted findings from the agent transcript.

    Returns a list of finding dicts with the shared 10-field schema and
    ``source='llm'``. Malformed output yields an empty list.
    """
    if not text or not text.strip():
        return []

    for candidate in _extract_candidates(text):
        data = _try_parse_json(candidate)
        if data is None:
            continue
        normalized: list[dict[str, Any]] = []
        for item in data:
            finding = _normalize_finding(item)
            if finding is not None:
                normalized.append(finding)
        if normalized:
            return normalized
    return []
