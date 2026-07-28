"""Regular-expression rules for local textual patterns."""

from __future__ import annotations

import re
from typing import Any

from models.finding import Finding
from parser.diff_parser import ChangedFile

from .base import Detector


class RegexDetector(Detector):
    def detect(self, changed_file: ChangedFile, rule: dict[str, Any]) -> list[Finding]:
        patterns = rule.get("pattern", [])
        if isinstance(patterns, str):
            patterns = [patterns]
        compiled = [re.compile(pattern) for pattern in patterns]
        return [
            _finding(changed_file.path, line.number, line.content, rule)
            for line in changed_file.changes
            if any(pattern.search(line.content) for pattern in compiled)
        ]


def _finding(path: str, line: int, evidence: str, rule: dict[str, Any]) -> Finding:
    return Finding(
        severity=rule["severity"],
        category=rule["category"],
        file=path,
        line=line,
        title=rule["message"],
        evidence=evidence[:500],
        recommendation=rule["recommendation"],
        confidence=float(rule.get("confidence", 0.8)),
        source="regex_detector",
        rule_id=rule["rule_id"],
        rule_version=str(rule.get("version", 1)),
        validation_status="pending" if rule.get("validator", {}).get("enabled") else "not_required",
    )
