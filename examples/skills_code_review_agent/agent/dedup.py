# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Finding deduplication and confidence-based routing."""

from typing import Any


def dedup_findings(findings: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Deduplicate findings by (file, line, category).

    Low-confidence findings (< 0.85) are routed to warnings.
    High-confidence findings are deduplicated.

    Each pool (findings, warnings) uses its own dedup set so a
    low-confidence hit never shadows a high-confidence one and vice versa.

    Returns (findings, warnings) tuple.
    """
    seen_high: set[tuple] = set()
    seen_warn: set[tuple] = set()
    high_conf: list[dict] = []
    warnings: list[dict] = []

    for f in findings:
        key = (f.get('file', ''), f.get('line', 0), f.get('category', ''))

        if f.get('confidence', 1.0) < 0.85:
            if key not in seen_warn:
                seen_warn.add(key)
                warnings.append(f)
        else:
            if key not in seen_high:
                seen_high.add(key)
                high_conf.append(f)

    return high_conf, warnings
