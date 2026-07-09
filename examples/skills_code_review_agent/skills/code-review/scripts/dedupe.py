#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Dedupe + confidence triage (Phase 4, L5).

Takes the raw diagnostics from P2 (rule matching) + P3 (sandbox) and:
  1. groups by ``(file, line, category)`` — same file/line/category collapses;
  2. within a group, keeps the highest-``confidence`` finding and merges
     multi-source reports (``source`` → ``rule+sandbox``, ``severity`` → max);
  3. triages by ``confidence`` into three buckets:
     ``findings`` (≥0.8), ``warnings`` (0.6–0.8), ``needs_human_review`` (<0.6);
  4. assembles the 9-field :class:`Finding`, with evidence masked + truncated
     and an actionable default recommendation per category.

Same line + different ``category`` is preserved (not merged) — two distinct
issue types on one line stay as two findings.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from run_checks import RawFinding

# NOTE: Finding/DedupeResult are defined in ``cr_models.py`` (zero-risk
# top-level) so the agent can build an empty ``DedupeResult`` for a blocked
# stage without importing *this* filtered Skill script. mask_secrets is still
# imported lazily *inside* ``dedupe`` (not at module load) for the same reason.
# We re-export them here so legacy ``from dedupe import Finding, DedupeResult``
# imports (tests, etc.) keep working.
from cr_models import DedupeResult, Finding  # noqa: E402

# severity rank for "take the highest" on multi-source merge
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_MAX_SEVERITY = "critical"

# Actionable default recommendations per category (used when the rule didn't
# supply one — RawFinding has no recommendation field).
_DEFAULT_RECOMMENDATIONS: dict[str, str] = {
    "security": "使用参数化查询/参数列表替代字符串拼接；密钥从环境变量或密钥管理服务读取",
    "sensitive": "立即轮换该凭证并从代码中移除，改用密钥管理服务注入",
    "async": "为协程调用补 await；异步资源用 async with 管理生命周期",
    "resource": "将 open()/connect() 放入 with 语句确保异常路径也释放",
    "db": "连接/游标用 with 管理；事务异常路径加 rollback，正常路径 commit",
    "tests": "为新增公开函数补充对应 test_<fn> 测试用例",
}

# Max evidence length kept in the final finding (avoids over-long reports).
_EVIDENCE_MAX_CHARS = 200


def _highest_severity(severities: list[str]) -> str:
    """Return the most severe from a list (critical > high > medium > low)."""
    best = "low"
    best_rank = -1
    for s in severities:
        r = _SEVERITY_RANK.get(s, 0)
        if r > best_rank:
            best, best_rank = s, r
    return best


def dedupe(raw_findings: "list[RawFinding]") -> DedupeResult:
    """Dedupe + triage raw diagnostics into three confidence buckets.

    Steps (ARCHITECTURE.md §9):
      1. group by ``(file, line, category)``;
      2. within a group keep the highest-confidence finding, merge sources
         (``rule+sandbox``) and take the max severity;
      3. mask + truncate evidence;
      4. triage by confidence into findings / warnings / needs_human_review.
    """
    # 1. group by (file, line, category)
    from mask_secrets import mask_secrets  # lazy: only when dedupe actually runs

    groups: dict[tuple[str, int, str], list] = {}
    for rf in raw_findings:
        key = (rf.file, rf.line, rf.category)
        groups.setdefault(key, []).append(rf)

    # 2. merge each group → one Finding
    merged: list[Finding] = []
    for key, group in groups.items():
        # highest-confidence finding is the "winner" (title/evidence/base)
        best = max(group, key=lambda f: f.confidence)
        # multi-source merge: union of sources, max severity
        sources = sorted({f.source for f in group})
        source = "+".join(sources) if len(sources) > 1 else sources[0] if sources else "rule"
        severity = _highest_severity([f.severity_hint for f in group])

        # 3. mask + truncate evidence (P3 sandbox already masked; local rule
        # evidence may carry secrets — run mask_secrets unconditionally).
        evidence, _ = mask_secrets(best.evidence or "")
        if len(evidence) > _EVIDENCE_MAX_CHARS:
            evidence = evidence[:_EVIDENCE_MAX_CHARS] + "…"

        recommendation = _DEFAULT_RECOMMENDATIONS.get(
            best.category, "检查并修复该问题"
        )

        merged.append(
            Finding(
                severity=severity,
                category=best.category,
                file=best.file,
                line=best.line,
                title=best.title,
                evidence=evidence,
                recommendation=recommendation,
                confidence=best.confidence,
                source=source,
            )
        )

    # 4. triage by confidence
    result = DedupeResult()
    for f in merged:
        if f.confidence >= 0.8:
            result.findings.append(f)
        elif f.confidence >= 0.6:
            result.warnings.append(f)
        else:
            result.needs_human_review.append(f)

    return result


# --------------------------------------------------------------------------- #
# CLI (stdin JSON list of RawFinding dicts → stdout JSON DedupeResult)
# --------------------------------------------------------------------------- #
def _raw_from_dict(d: dict):
    """Reconstruct a RawFinding-like object from a JSON dict."""
    from dataclasses import make_dataclass

    RawLike = make_dataclass(
        "RawLike",
        ["category", "file", "line", "title", "evidence", "severity_hint",
         "confidence", "source"],
        defaults=["rule"],
    )
    return RawLike(
        category=d["category"], file=d["file"], line=d["line"], title=d["title"],
        evidence=d["evidence"], severity_hint=d["severity_hint"],
        confidence=d["confidence"], source=d.get("source", "rule"),
    )


def main(argv: list[str] | None = None) -> int:
    import json
    import sys

    data = json.loads(sys.stdin.read())
    raws = [_raw_from_dict(d) for d in data]
    res = dedupe(raws)
    out = {
        "findings": [f.__dict__ for f in res.findings],
        "warnings": [f.__dict__ for f in res.warnings],
        "needs_human_review": [f.__dict__ for f in res.needs_human_review],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
