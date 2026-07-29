#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Stable finding deduplication and confidence buckets."""

from __future__ import annotations

import math
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any

from code_review.redaction import redact_data


class FindingBucket(str, Enum):
    """Confidence-owned finding destinations."""

    FINDINGS = "findings"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    SUPPRESSED = "suppressed"


_SEVERITY_RANK = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "low": 2,
    "info": 1,
}
_SOURCES = {"rule-engine", "ast", "heuristic"}
_REQUIRED_FINDING_FIELDS = {
    "severity",
    "category",
    "file",
    "line",
    "title",
    "evidence",
    "recommendation",
    "confidence",
    "source",
    "rule_id",
}


@dataclass(frozen=True)
class BucketedFindings:
    """Stable four-bucket result for pipeline, report, and persistence layers."""

    findings: tuple[dict[str, Any], ...]
    needs_human_review: tuple[dict[str, Any], ...]
    suppressed: tuple[dict[str, Any], ...]
    warnings: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        """返回与内部状态隔离、可 JSON 序列化的规范化分桶结果。"""

        return {
            "findings": deepcopy(list(self.findings)),
            "needs_human_review": deepcopy(list(self.needs_human_review)),
            "suppressed": deepcopy(list(self.suppressed)),
            "warnings": deepcopy(list(self.warnings)),
        }


def bucket_for_confidence(confidence: float) -> FindingBucket:
    """按锁定置信度边界将候选项映射到互斥分桶。"""

    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be a number between 0 and 1")
    normalized = float(confidence)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValueError("confidence must be a number between 0 and 1")
    if normalized >= 0.80:
        return FindingBucket.FINDINGS
    if normalized >= 0.50:
        return FindingBucket.NEEDS_HUMAN_REVIEW
    return FindingBucket.SUPPRESSED


def _string_field(
    candidate: Mapping[str, Any],
    name: str,
    *,
    allow_empty: bool = False,
) -> str:
    """读取并校验候选 finding 中指定的字符串字段。"""

    value = candidate[name]
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if not allow_empty and not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _normalize_also_matched(extra: Mapping[str, Any]) -> set[str]:
    """规范化去重合并后的附加规则标识集合。"""

    value = extra.get("also_matched", ())
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError("extra.also_matched must be a sequence of rule ids")
    rule_ids: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("extra.also_matched must contain rule ids")
        rule_ids.add(item)
    return rule_ids


def _normalize_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """脱敏并校验单个候选 finding，使其符合内部字段契约。"""

    missing = sorted(_REQUIRED_FINDING_FIELDS - set(candidate))
    if missing:
        raise ValueError(f"finding is missing required fields: {missing}")

    safe = redact_data(candidate)
    severity = _string_field(safe, "severity")
    if severity not in _SEVERITY_RANK:
        raise ValueError("severity is invalid")
    source = _string_field(safe, "source")
    if source not in _SOURCES:
        raise ValueError("source is invalid")
    confidence = safe["confidence"]
    bucket_for_confidence(confidence)

    line = safe["line"]
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        raise ValueError("line must identify a real source line")

    line_side = safe.get("line_side", "new")
    if line_side not in {"new", "old"}:
        raise ValueError("line_side is invalid")

    raw_extra = safe.get("extra", {})
    if not isinstance(raw_extra, Mapping):
        raise ValueError("extra must be an object")
    extra = dict(raw_extra)
    extra["also_matched"] = sorted(_normalize_also_matched(extra))
    try:
        json.dumps(extra, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("extra must contain JSON-serializable values") from exc

    return {
        "severity": severity,
        "category": _string_field(safe, "category"),
        "file": _string_field(safe, "file"),
        "line": line,
        "title": _string_field(safe, "title"),
        "evidence": _string_field(safe, "evidence", allow_empty=True),
        "recommendation": _string_field(safe, "recommendation"),
        "confidence": float(confidence),
        "source": source,
        "line_side": line_side,
        "rule_id": _string_field(safe, "rule_id"),
        "extra": extra,
    }


def _evidence_specificity(evidence: str) -> tuple[int, int, int]:
    """返回证据具体程度的稳定排序代理值。"""

    normalized = " ".join(evidence.split())
    non_whitespace_length = sum(not character.isspace() for character in evidence)
    return non_whitespace_length, len(normalized.split()), len(normalized)


def _primary_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    """构造同一去重组内选择主 finding 的稳定优先级键。"""

    specificity = _evidence_specificity(candidate["evidence"])
    return (
        -_SEVERITY_RANK[candidate["severity"]],
        -candidate["confidence"],
        -specificity[0],
        -specificity[1],
        -specificity[2],
        candidate["rule_id"],
        candidate["source"],
        candidate["title"],
        candidate["recommendation"],
        candidate["line_side"],
        json.dumps(
            candidate,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def _output_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    """构造最终报告中 finding 的稳定输出排序键。"""

    return (
        -_SEVERITY_RANK[candidate["severity"]],
        candidate["file"],
        candidate["line"],
        candidate["category"],
        candidate["rule_id"],
    )


def _dedup_key(candidate: Mapping[str, Any]) -> tuple[str, int, str]:
    """返回规格锁定的文件、行号、类别三元去重键。"""

    return candidate["file"], candidate["line"], candidate["category"]


def deduplicate_findings(
    candidates: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """按文件、行号、类别三元组稳定去重候选 finding。"""

    groups: dict[
        tuple[str, int, str],
        list[dict[str, Any]],
    ] = defaultdict(list)
    for candidate in candidates:
        normalized = _normalize_candidate(candidate)
        groups[_dedup_key(normalized)].append(normalized)

    findings: list[dict[str, Any]] = []
    for group_key in sorted(groups):
        group = sorted(groups[group_key], key=_primary_key)
        primary = deepcopy(group[0])
        primary_rule_id = primary["rule_id"]

        also_matched: set[str] = set()
        for candidate in group:
            also_matched.update(_normalize_also_matched(candidate["extra"]))
            if candidate["rule_id"] != primary_rule_id:
                also_matched.add(candidate["rule_id"])
        also_matched.discard(primary_rule_id)

        extra = dict(primary["extra"])
        extra["also_matched"] = sorted(also_matched)
        primary["extra"] = extra
        primary["bucket"] = bucket_for_confidence(
            primary["confidence"]
        ).value
        primary["dedup_key"] = (
            f"{primary['file']}:{primary['line']}:{primary['category']}"
        )
        findings.append(primary)

    return tuple(sorted(findings, key=_output_key))


def _normalize_warning(warning: Mapping[str, Any]) -> dict[str, str]:
    """脱敏并校验运行或治理 warning 的最小字段。"""

    if "code" not in warning or "message" not in warning:
        raise ValueError("runtime warning requires code and message")
    safe = redact_data(warning)
    code = _string_field(safe, "code")
    message = _string_field(safe, "message")
    normalized = {
        "code": code,
        "message": message,
    }
    if "stage" in safe:
        normalized["stage"] = _string_field(safe, "stage")
    return normalized


def route_findings(
    candidates: Iterable[Mapping[str, Any]],
    *,
    warnings: Iterable[Mapping[str, Any]] = (),
) -> BucketedFindings:
    """去重候选项后按置信度分桶，并独立保留运行告警。"""

    buckets: dict[FindingBucket, list[dict[str, Any]]] = {
        FindingBucket.FINDINGS: [],
        FindingBucket.NEEDS_HUMAN_REVIEW: [],
        FindingBucket.SUPPRESSED: [],
    }
    for finding in deduplicate_findings(candidates):
        bucket = FindingBucket(finding["bucket"])
        buckets[bucket].append(finding)

    normalized_warnings = tuple(
        sorted(
            (_normalize_warning(warning) for warning in warnings),
            key=lambda warning: (
                warning["code"],
                warning.get("stage", ""),
                warning["message"],
            ),
        )
    )
    return BucketedFindings(
        findings=tuple(buckets[FindingBucket.FINDINGS]),
        needs_human_review=tuple(
            buckets[FindingBucket.NEEDS_HUMAN_REVIEW]
        ),
        suppressed=tuple(buckets[FindingBucket.SUPPRESSED]),
        warnings=normalized_warnings,
    )


__all__ = [
    "BucketedFindings",
    "FindingBucket",
    "bucket_for_confidence",
    "deduplicate_findings",
    "route_findings",
]
