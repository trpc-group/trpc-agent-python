# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Report generator for the Tool Script Safety Guard.

Produces a human-readable and machine-readable JSON report from a
``SafetyScanReport``.

Usage::

    from trpc_agent_sdk.tools.safety import SafetyScanner, ReportGenerator

    scanner = SafetyScanner()
    report = scanner.scan(...)
    generator = ReportGenerator()
    json_str = generator.to_json(report)
    generator.save(report, "/tmp/safety_report.json")
"""

from __future__ import annotations

import json
from pathlib import Path
from ._types import SafetyScanReport


class ReportGenerator:
    """Serialises a ``SafetyScanReport`` to JSON and optionally writes it to disk."""

    @staticmethod
    def to_json(report: SafetyScanReport, indent: int = 2) -> str:
        """Convert the report to a pretty-printed JSON string."""
        return json.dumps(report.to_dict(), indent=indent, ensure_ascii=False, default=str)

    @staticmethod
    def to_dict(report: SafetyScanReport) -> dict:
        """Return the report as a plain Python dictionary (alias of ``report.to_dict()``)."""
        return report.to_dict()

    @staticmethod
    def save(report: SafetyScanReport, file_path: str, indent: int = 2) -> None:
        """Write the report as JSON to *file_path*."""
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(ReportGenerator.to_json(report, indent=indent))


def generate_report_json(report: SafetyScanReport) -> str:
    """Shortcut: return JSON string for *report*."""
    return ReportGenerator.to_json(report)


def save_report(report: SafetyScanReport, file_path: str) -> None:
    """Shortcut: persist *report* to *file_path*."""
    ReportGenerator.save(report, file_path)
