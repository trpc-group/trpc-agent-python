# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Scanner interface for the Tool Script Safety Guard."""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from ..models import RiskFinding
from ..models import ScanInput
from ..policy import SafetyPolicy


class ScannerABC(ABC):
    """A scanner turns a :class:`ScanInput` into a list of :class:`RiskFinding`.

    Implementations must be pure and side-effect free.
    """

    #: Language token this scanner handles (e.g. "python", "bash").
    language: str = "unknown"

    @abstractmethod
    def scan(self, scan_input: ScanInput, policy: SafetyPolicy) -> list[RiskFinding]:
        """Return findings for ``scan_input`` under ``policy`` (never raises)."""
        raise NotImplementedError


def dedupe_findings(findings: list[RiskFinding]) -> list[RiskFinding]:
    """Drop duplicate findings sharing the same (rule_id, line, snippet)."""
    seen: set[tuple[str, int, str]] = set()
    unique: list[RiskFinding] = []
    for f in findings:
        key = (f.rule_id, f.evidence.line, f.evidence.snippet)
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    return unique
