#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared, zero-risk data contracts for the code-review Skill.

``Finding`` and ``DedupeResult`` are deliberately defined here — *not* inside
``dedupe.py`` — so that the agent can build an empty ``DedupeResult`` for a
blocked stage (Filter deny / needs_human_review, or a skipped parse stage)
WITHOUT importing ``dedupe.py`` (a filtered Skill script) at all. Importing a
filtered script's module — even just to grab a dataclass — would defeat the
pre-execution gate (see ARCHITECTURE.md §7). This module has no side effects
and imports nothing risky, so it is always safe to import.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field


@dataclass
class Finding:
    """One structured finding — the 9-field contract (ARCHITECTURE.md §12)."""

    severity: str  # critical|high|medium|low
    category: str
    file: str
    line: int
    title: str
    evidence: str
    recommendation: str
    confidence: float
    source: str  # rule|sandbox|llm|rule+sandbox|...


@dataclass
class DedupeResult:
    """Triage output — three confidence buckets."""

    findings: list[Finding] = field(default_factory=list)  # confidence >= 0.8
    warnings: list[Finding] = field(default_factory=list)  # 0.6 <= conf < 0.8
    needs_human_review: list[Finding] = field(default_factory=list)  # conf < 0.6

    @property
    def total(self) -> int:
        return len(self.findings) + len(self.warnings) + len(self.needs_human_review)

    def severity_counts(self) -> dict[str, int]:
        """Count findings by severity across all three buckets."""
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for f in (*self.findings, *self.warnings, *self.needs_human_review):
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts

    def all_with_bucket(self) -> list[tuple[Finding, str]]:
        """Yield (finding, bucket) for every finding — for storage into the
        ``finding`` table where ``bucket`` distinguishes the three tiers."""
        return (
            [(f, "findings") for f in self.findings]
            + [(f, "warnings") for f in self.warnings]
            + [(f, "needs_human_review") for f in self.needs_human_review]
        )
