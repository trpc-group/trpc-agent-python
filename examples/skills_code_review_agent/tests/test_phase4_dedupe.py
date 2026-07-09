# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Phase 4 (Dedupe & structuring) acceptance tests.

Covers the Phase-4 DoD:
  1. Same (file,line,category) collapses to one (highest confidence).
  2. Same line + different category is preserved.
  3. confidence < 0.6 → needs_human_review; 0.6–0.8 → warnings; ≥0.8 → findings.
  4. Finding has all 9 fields; evidence is masked.
  5. Multi-source merge: source="rule+sandbox", severity takes the max.
  6. Evidence is truncated to a sane length.
  7. Three buckets persist to the finding table (bucket column).

Run:
    C:/Users/douzhenyu/.workbuddy/binaries/python/envs/cr_agent/Scripts/python.exe \\
        examples/skills_code_review_agent/tests/test_phase4_dedupe.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_EXAMPLE_ROOT))
_SCRIPTS_DIR = _EXAMPLE_ROOT / "skills" / "code-review" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from agent.db import SQLiteStore  # noqa: E402
from dedupe import DedupeResult  # noqa: E402
from dedupe import Finding  # noqa: E402
from dedupe import dedupe  # noqa: E402
from run_checks import RawFinding  # noqa: E402


def _rf(category, file, line, title, evidence, sev, conf, source="rule"):
    return RawFinding(category, file, line, title, evidence, sev, conf, source)


class TestDedupeGrouping(unittest.TestCase):
    """DoD #1-2 — same key collapses, different category preserved."""

    def test_same_key_keeps_highest_confidence(self):
        raws = [
            _rf("security", "a.py", 5, "sqli", "execute(q+x)", "critical", 0.9),
            _rf("security", "a.py", 5, "sqli dup", "execute(q+x) dup", "high", 0.7),
        ]
        res = dedupe(raws)
        self.assertEqual(res.total, 1)
        self.assertEqual(res.findings[0].confidence, 0.9)
        self.assertEqual(res.findings[0].title, "sqli")  # winner's title

    def test_same_line_different_category_preserved(self):
        raws = [
            _rf("security", "a.py", 5, "sqli", "execute(q+x)", "critical", 0.9),
            _rf("sensitive", "a.py", 5, "key", "API_KEY=x", "critical", 0.95),
        ]
        res = dedupe(raws)
        self.assertEqual(res.total, 2)
        cats = {f.category for f in res.findings}
        self.assertEqual(cats, {"security", "sensitive"})

    def test_duplicate_sample_merges_to_one(self):
        """P6 'duplicate finding' sample: two same-category reports → one."""
        raws = [
            _rf("async", "x.py", 10, "unawaited", "foo()", "high", 0.7, "rule"),
            _rf("async", "x.py", 10, "unawaited", "foo() [sb]", "high", 0.8, "sandbox"),
        ]
        res = dedupe(raws)
        self.assertEqual(res.total, 1)
        self.assertEqual(res.findings[0].confidence, 0.8)


class TestTriageBuckets(unittest.TestCase):
    """DoD #3 — confidence thresholds route to the right bucket."""

    def test_below_0_6_to_needs_human_review(self):
        res = dedupe([_rf("tests", "a.py", 1, "t", "e", "low", 0.4)])
        self.assertEqual(len(res.needs_human_review), 1)
        self.assertEqual(len(res.findings), 0)
        self.assertEqual(len(res.warnings), 0)

    def test_0_6_to_0_8_to_warnings(self):
        res = dedupe([_rf("async", "a.py", 1, "t", "e", "medium", 0.65)])
        self.assertEqual(len(res.warnings), 1)
        self.assertEqual(len(res.findings), 0)

    def test_above_0_8_to_findings(self):
        res = dedupe([_rf("security", "a.py", 1, "t", "e", "critical", 0.95)])
        self.assertEqual(len(res.findings), 1)
        self.assertEqual(len(res.warnings), 0)

    def test_boundary_0_6_is_warnings(self):
        res = dedupe([_rf("x", "a.py", 1, "t", "e", "low", 0.6)])
        self.assertEqual(len(res.warnings), 1)

    def test_boundary_0_8_is_findings(self):
        res = dedupe([_rf("x", "a.py", 1, "t", "e", "low", 0.8)])
        self.assertEqual(len(res.findings), 1)


class TestFindingFields(unittest.TestCase):
    """DoD #4 — Finding has 9 fields, evidence masked."""

    def test_finding_has_nine_fields(self):
        res = dedupe([_rf("security", "a.py", 5, "sqli", "execute(q+x)", "critical", 0.9)])
        f = res.findings[0]
        for field in ("severity", "category", "file", "line", "title",
                      "evidence", "recommendation", "confidence", "source"):
            self.assertTrue(hasattr(f, field), f"missing field: {field}")

    def test_evidence_masked(self):
        secret = "sk-1234567890abcdef1234567890abcdef"  # 32 chars, matches sk- pattern
        res = dedupe([_rf("sensitive", "a.py", 1, "key", f"token={secret}", "critical", 0.95)])
        self.assertIn("***REDACTED***", res.findings[0].evidence)
        self.assertNotIn(secret, res.findings[0].evidence)

    def test_recommendation_default_per_category(self):
        res = dedupe([_rf("db", "a.py", 1, "t", "e", "high", 0.85)])
        self.assertTrue(res.findings[0].recommendation)  # non-empty, actionable

    def test_severity_from_hint(self):
        res = dedupe([_rf("security", "a.py", 1, "t", "e", "critical", 0.9)])
        self.assertEqual(res.findings[0].severity, "critical")


class TestMultiSourceMerge(unittest.TestCase):
    """DoD #5 — rule+sandbox merge, severity takes the max."""

    def test_source_concatenated(self):
        raws = [
            _rf("security", "a.py", 5, "sqli", "ev1", "high", 0.7, "rule"),
            _rf("security", "a.py", 5, "sqli", "ev2", "critical", 0.85, "sandbox"),
        ]
        res = dedupe(raws)
        self.assertEqual(res.total, 1)
        f = res.findings[0]
        self.assertEqual(f.source, "rule+sandbox")

    def test_severity_takes_highest(self):
        raws = [
            _rf("security", "a.py", 5, "sqli", "ev1", "medium", 0.9, "rule"),
            _rf("security", "a.py", 5, "sqli", "ev2", "critical", 0.85, "sandbox"),
        ]
        res = dedupe(raws)
        self.assertEqual(res.findings[0].severity, "critical")

    def test_single_source_not_concatenated(self):
        res = dedupe([_rf("security", "a.py", 1, "t", "e", "high", 0.9, "rule")])
        self.assertEqual(res.findings[0].source, "rule")


class TestEvidenceTruncation(unittest.TestCase):
    """Risk note — evidence capped to a sane length."""

    def test_long_evidence_truncated(self):
        long_ev = "x" * 500
        res = dedupe([_rf("security", "a.py", 1, "t", long_ev, "high", 0.9)])
        self.assertLessEqual(len(res.findings[0].evidence), 201)  # 200 + ellipsis


class TestPersistFindings(unittest.IsolatedAsyncioTestCase):
    """DoD #7 — three buckets persist to the finding table (bucket column)."""

    async def test_all_buckets_persisted(self):
        raws = [
            _rf("security", "a.py", 5, "sqli", "execute(q+x)", "critical", 0.95),
            _rf("async", "b.py", 2, "unawaited", "foo()", "high", 0.7),
            _rf("tests", "c.py", 1, "notest", "def f()", "low", 0.4),
        ]
        res = dedupe(raws)
        store = SQLiteStore(":memory:")
        try:
            tid = await store.create_task("diff", "x.diff", "dry-run")
            for finding, bucket in res.all_with_bucket():
                await store.add_finding(
                    tid, finding.severity, finding.category, finding.file,
                    finding.line, finding.title, finding.evidence,
                    finding.recommendation, finding.confidence,
                    finding.source, bucket,
                )
            rec = await store.get_task(tid)
            findings = rec["findings"]
            self.assertEqual(len(findings), 3)
            buckets = {f["bucket"] for f in findings}
            self.assertEqual(buckets, {"findings", "warnings", "needs_human_review"})
            # severity counts via the dedupe result
            counts = res.severity_counts()
            self.assertEqual(counts["critical"], 1)
            self.assertEqual(counts["high"], 1)
            self.assertEqual(counts["low"], 1)
        finally:
            await store.close()


class TestDedupeResultHelpers(unittest.TestCase):
    """DedupeResult utility methods."""

    def test_all_with_bucket_order(self):
        res = dedupe([
            _rf("a", "f.py", 1, "t", "e", "high", 0.9),
            _rf("b", "f.py", 2, "t", "e", "medium", 0.7),
            _rf("c", "f.py", 3, "t", "e", "low", 0.5),
        ])
        pairs = res.all_with_bucket()
        self.assertEqual(len(pairs), 3)
        self.assertEqual(pairs[0][1], "findings")
        self.assertEqual(pairs[1][1], "warnings")
        self.assertEqual(pairs[2][1], "needs_human_review")

    def test_empty_input(self):
        res = dedupe([])
        self.assertEqual(res.total, 0)
        self.assertEqual(res.severity_counts(), {"critical": 0, "high": 0, "medium": 0, "low": 0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
