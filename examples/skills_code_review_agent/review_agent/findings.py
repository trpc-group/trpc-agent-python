# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Finding triage: deterministic decision table, two-level dedup, redaction.

Decision table (documented in DESIGN.md; deliberately not a numeric score):

| evidence                         | disposition                                    |
|----------------------------------|------------------------------------------------|
| static, precision=high           | findings                                       |
| static, precision=low, LLM ran   | LLM verdict decides (reject -> warnings)       |
| static, precision=low, no LLM    | injection/secret categories -> findings        |
|                                  | style/test categories -> warnings              |
| LLM additional finding           | kept only when its evidence quotes the diff    |
| static hit + LLM reject          | high-risk category -> needs_human_review,      |
|                                  | otherwise -> warnings                          |

Dedup is two-level:
1. exact: sha256(rule_id|file|line|normalized evidence) — idempotent across
   sources and runs; enforced again by UNIQUE(task_id, dedup_key) in the DB;
2. semantic: same (file, line, category) collapses to the most severe rule;
   losers are kept as ``suppressed`` rows so the collapse stays auditable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional

from .redactor import Redactor
from .store import Finding

SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
#: categories where we prefer recall over precision when no LLM is available
HIGH_RISK_CATEGORIES = {"security", "secrets"}
#: categories where we prefer precision (they drown reports when noisy)
NOISY_CATEGORIES = {"missing_tests"}

STATUS_REPORTED = "reported"
STATUS_WARNING = "warning"
STATUS_HUMAN = "needs_human_review"
STATUS_SUPPRESSED = "suppressed"


def _normalize_evidence(evidence: str) -> str:
    return re.sub(r"\s+", " ", (evidence or "").strip())[:80]


def dedup_key(rule_id: str, file: str, line: int, evidence: str) -> str:
    raw = f"{rule_id}|{file}|{line}|{_normalize_evidence(evidence)}"
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass
class TriageResult:
    reported: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    needs_human: list[Finding] = field(default_factory=list)
    suppressed: list[Finding] = field(default_factory=list)

    @property
    def all_rows(self) -> list[Finding]:
        return self.reported + self.warnings + self.needs_human + self.suppressed

    def severity_dist(self) -> dict:
        dist: dict[str, int] = {}
        for row in self.reported:
            dist[row.severity] = dist.get(row.severity, 0) + 1
        return dist


def _match_verdict(raw: dict, verdicts: list[dict]) -> Optional[str]:
    """Find the LLM verdict for a static finding (by rule_id+file, line ±2)."""
    for verdict in verdicts:
        if str(verdict.get("rule_id", "")) != str(raw.get("rule_id", "")):
            continue
        if str(verdict.get("file", "")) != str(raw.get("file", "")):
            continue
        try:
            delta = abs(int(verdict.get("line", -999)) - int(raw.get("line", 0)))
        except (TypeError, ValueError):
            delta = 999
        if delta <= 2:
            value = str(verdict.get("verdict", "")).lower()
            if value in ("confirm", "reject", "uncertain"):
                return value
    return None


def _quote_matches_diff(evidence: str, diff_text: str) -> bool:
    """LLM additional findings must quote a real line from the diff."""
    needle = _normalize_evidence(evidence)
    if len(needle) < 8:
        return False
    hay = re.sub(r"\s+", " ", diff_text)
    return needle in hay


def triage(*,
           task_id: str,
           static_findings: list[dict],
           llm_verdicts: Optional[list[dict]] = None,
           llm_additional: Optional[list[dict]] = None,
           diff_text: str = "",
           redactor: Optional[Redactor] = None,
           llm_ran: bool = False) -> TriageResult:
    """Apply the decision table, then the two dedup levels, then redaction."""
    verdicts = llm_verdicts or []
    result = TriageResult()

    candidates: list[tuple[dict, str]] = []  # (raw finding, status)

    for raw in static_findings:
        precision = str(raw.get("precision", "low"))
        category = str(raw.get("category", ""))
        verdict = _match_verdict(raw, verdicts) if llm_ran else None

        if verdict == "reject":
            status = STATUS_HUMAN if category in HIGH_RISK_CATEGORIES else STATUS_WARNING
        elif precision == "high":
            status = STATUS_REPORTED
        elif llm_ran and verdict == "confirm":
            status = STATUS_REPORTED
        elif llm_ran and verdict is None:
            # low-precision, LLM saw it but did not judge it -> keep cautious
            status = STATUS_WARNING
        elif llm_ran and verdict == "uncertain":
            status = STATUS_HUMAN
        else:  # no LLM (dry-run): per-category recall/precision preference
            if category in HIGH_RISK_CATEGORIES:
                status = STATUS_REPORTED
            elif category in NOISY_CATEGORIES or str(raw.get("severity")) == "info":
                status = STATUS_WARNING
            else:
                status = STATUS_HUMAN if str(raw.get("confidence")) == "low" else STATUS_WARNING
        # info-severity items are advisories, never defects: cap them at
        # warnings even when an LLM verdict confirms they are factually true
        if str(raw.get("severity")) == "info" and status == STATUS_REPORTED:
            status = STATUS_WARNING
        candidates.append((raw, status))

    for raw in (llm_additional or []):
        if not _quote_matches_diff(str(raw.get("evidence", "")), diff_text):
            continue  # unverifiable LLM claim: dropped, never reported
        raw = dict(raw)
        raw.setdefault("rule_id", "LLM000")
        raw.setdefault("confidence", "medium")
        raw["source"] = "llm"
        candidates.append((raw, STATUS_REPORTED))

    # level 1: exact dedup
    seen_exact: dict[str, tuple[dict, str]] = {}
    for raw, status in candidates:
        key = dedup_key(str(raw.get("rule_id")), str(raw.get("file")), int(raw.get("line") or 0),
                        str(raw.get("evidence", "")))
        if key in seen_exact:
            continue
        raw["_dedup_key"] = key
        seen_exact[key] = (raw, status)

    # level 2: same (file, line, category) -> keep the most severe as primary
    grouped: dict[tuple, list[tuple[dict, str]]] = {}
    for raw, status in seen_exact.values():
        group_key = (str(raw.get("file")), int(raw.get("line") or 0), str(raw.get("category")))
        grouped.setdefault(group_key, []).append((raw, status))

    def _rank(item: tuple[dict, str]) -> tuple:
        raw, status = item
        return (SEVERITY_ORDER.get(str(raw.get("severity")),
                                   0), status == STATUS_REPORTED, str(raw.get("precision")) == "high")

    for group in grouped.values():
        group.sort(key=_rank, reverse=True)
        primary_raw, primary_status = group[0]
        merged = [str(raw.get("rule_id")) for raw, _ in group[1:]]
        if merged:
            primary_raw = dict(primary_raw)
            primary_raw["merged_rules"] = merged
        _append(result, task_id, primary_raw, primary_status, redactor)
        for raw, _status in group[1:]:
            _append(result, task_id, raw, STATUS_SUPPRESSED, redactor)

    return result


def _append(result: TriageResult, task_id: str, raw: dict, status: str, redactor: Optional[Redactor]) -> None:

    def _clean(text: str) -> str:
        text = str(text or "")
        return redactor.redact(text).text if redactor else text

    fix = raw.get("fix_snippet")
    if isinstance(fix, dict):
        fix = {key: _clean(value) for key, value in fix.items()}
        if raw.get("merged_rules"):
            fix["merged_rules"] = raw["merged_rules"]
    elif raw.get("merged_rules"):
        fix = {"merged_rules": raw["merged_rules"]}

    row = Finding(
        task_id=task_id,
        dedup_key=str(
            raw.get("_dedup_key") or dedup_key(str(raw.get("rule_id")), str(raw.get("file")), int(raw.get("line") or 0),
                                               str(raw.get("evidence", "")))),
        rule_id=str(raw.get("rule_id", "")),
        category=str(raw.get("category", "")),
        severity=str(raw.get("severity", "info")),
        confidence=str(raw.get("confidence", "low")),
        source=str(raw.get("source", "static")),
        file=str(raw.get("file", "")),
        line=int(raw.get("line") or 0),
        title=_clean(raw.get("title", ""))[:500],
        evidence=_clean(raw.get("evidence", ""))[:2000],
        recommendation=_clean(raw.get("recommendation", ""))[:2000],
        fix_json=fix,
        status=status,
    )
    bucket = {
        STATUS_REPORTED: result.reported,
        STATUS_WARNING: result.warnings,
        STATUS_HUMAN: result.needs_human,
        STATUS_SUPPRESSED: result.suppressed,
    }[status]
    bucket.append(row)
