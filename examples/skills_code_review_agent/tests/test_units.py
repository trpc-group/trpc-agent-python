# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests: diff parsing edges, decision-table triage, two-level dedup."""

from __future__ import annotations

from review_agent.diff_parser import parse_diff, parse_diff_text
from review_agent.findings import STATUS_REPORTED, STATUS_SUPPRESSED, triage
from review_agent.redactor import Redactor


class TestDiffParser:

    def test_rename_and_delete(self):
        diff = ("diff --git a/src/old.py b/src/new.py\n"
                "similarity index 100%\n"
                "rename from src/old.py\n"
                "rename to src/new.py\n"
                "diff --git a/gone.py b/gone.py\n"
                "deleted file mode 100644\n"
                "--- a/gone.py\n"
                "+++ /dev/null\n"
                "@@ -1,2 +0,0 @@\n"
                "-x = 1\n"
                "-y = 2\n")
        parsed = parse_diff.parse_unified_diff(diff)
        by_path = {f["path"]: f for f in parsed["files"]}
        assert by_path["src/new.py"]["change_type"] == "renamed"
        assert by_path["src/new.py"]["old_path"] == "src/old.py"
        assert by_path["gone.py"]["change_type"] == "deleted"

    def test_binary_marker(self):
        diff = ("diff --git a/logo.png b/logo.png\n"
                "Binary files a/logo.png and b/logo.png differ\n")
        parsed = parse_diff.parse_unified_diff(diff)
        assert parsed["files"][0]["is_binary"] is True

    def test_no_newline_marker_and_crlf(self):
        diff = ("diff --git a/w.py b/w.py\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/w.py\n"
                "@@ -0,0 +1,2 @@\n"
                "+a = 1\r\n"
                "+b = 2\n"
                "\\ No newline at end of file\n")
        parsed = parse_diff.parse_unified_diff(diff)
        entry = parsed["files"][0]
        assert len(entry["hunks"][0]["lines"]) == 2
        content, complete = parse_diff.reconstruct_post_image(entry)
        assert complete is True
        assert "a = 1" in content

    def test_garbage_is_skipped_not_raised(self):
        parsed = parse_diff.parse_unified_diff("@@ -1,1 +1,1 @@\n+orphan hunk\nrandom noise\n")
        assert parsed["files"] == []
        assert parsed["errors"], "orphan hunk must be recorded"

    def test_gap_filling_keeps_line_numbers(self):
        diff = ("diff --git a/m.py b/m.py\n"
                "--- a/m.py\n"
                "+++ b/m.py\n"
                "@@ -1,2 +1,2 @@\n"
                " import os\n"
                "+x = eval(data)\n"
                "@@ -10,2 +10,2 @@\n"
                " def later():\n"
                "+    y = 2\n")
        parsed = parse_diff_text(diff)
        entry = parsed.payload["files"][0]
        lines = entry["content"].splitlines()
        assert lines[1] == "x = eval(data)"  # line 2
        assert lines[10] == "    y = 2"  # line 11, gap lines blank
        assert entry["candidate_lines"] == [2, 11]


def _raw(rule_id="SEC001",
         category="security",
         severity="critical",
         precision="high",
         file="a.py",
         line=3,
         evidence="db.execute(f'..{x}..')",
         confidence="high"):
    return {
        "rule_id": rule_id,
        "category": category,
        "severity": severity,
        "precision": precision,
        "file": file,
        "line": line,
        "title": "t",
        "evidence": evidence,
        "recommendation": "r",
        "confidence": confidence,
        "source": "static",
        "fix_snippet": None,
    }


class TestDecisionTable:

    def test_high_precision_static_reported(self):
        result = triage(task_id="t", static_findings=[_raw()], redactor=Redactor())
        assert len(result.reported) == 1 and result.reported[0].status == STATUS_REPORTED

    def test_low_precision_high_risk_reported_in_dry_run(self):
        result = triage(task_id="t", static_findings=[_raw(precision="low", category="secrets")])
        assert len(result.reported) == 1  # recall-first for high-risk categories

    def test_low_precision_noisy_category_goes_to_warnings(self):
        result = triage(task_id="t", static_findings=[_raw(precision="low", category="missing_tests", severity="info")])
        assert len(result.warnings) == 1 and not result.reported

    def test_llm_reject_high_risk_becomes_human_review(self):
        verdict = [{"rule_id": "SEC001", "file": "a.py", "line": 3, "verdict": "reject"}]
        result = triage(task_id="t", static_findings=[_raw()], llm_verdicts=verdict, llm_ran=True)
        assert len(result.needs_human) == 1 and not result.reported

    def test_llm_reject_low_risk_becomes_warning(self):
        raw = _raw(category="resource_leak", rule_id="RES001", severity="high")
        verdict = [{"rule_id": "RES001", "file": "a.py", "line": 3, "verdict": "reject"}]
        result = triage(task_id="t", static_findings=[raw], llm_verdicts=verdict, llm_ran=True)
        assert len(result.warnings) == 1 and not result.reported

    def test_llm_additional_requires_diff_quote(self):
        additional = [{
            "category": "security",
            "severity": "high",
            "file": "a.py",
            "line": 9,
            "title": "made up",
            "evidence": "this line is nowhere in the diff",
            "recommendation": "x",
        }]
        result = triage(task_id="t",
                        static_findings=[],
                        llm_additional=additional,
                        diff_text="actual content only",
                        llm_ran=True)
        assert not result.reported, "unverifiable LLM claims must be dropped"

    def test_llm_additional_with_real_quote_kept(self):
        additional = [{
            "category": "security",
            "severity": "high",
            "file": "a.py",
            "line": 9,
            "title": "real",
            "evidence": "os.system(user_cmd)",
            "recommendation": "x",
        }]
        result = triage(task_id="t",
                        static_findings=[],
                        llm_additional=additional,
                        diff_text="def f():\n    os.system(user_cmd)\n",
                        llm_ran=True)
        assert len(result.reported) == 1 and result.reported[0].source == "llm"


class TestDedup:

    def test_exact_duplicates_collapse(self):
        result = triage(task_id="t", static_findings=[_raw(), _raw()])
        assert len(result.reported) == 1 and not result.suppressed

    def test_same_line_same_category_merges_keeping_most_severe(self):
        first = _raw(rule_id="SECRET003", category="secrets", severity="critical", evidence="token1")
        second = _raw(rule_id="SECRET012", category="secrets", severity="high", evidence="token2")
        result = triage(task_id="t", static_findings=[first, second])
        assert len(result.reported) == 1
        assert result.reported[0].rule_id == "SECRET003"
        assert result.reported[0].fix_json["merged_rules"] == ["SECRET012"]
        assert len(result.suppressed) == 1
        assert result.suppressed[0].status == STATUS_SUPPRESSED

    def test_different_categories_same_line_not_merged(self):
        first = _raw(rule_id="SEC001", category="security")
        second = _raw(rule_id="DB003", category="db_lifecycle", severity="high", evidence="conn.commit missing")
        result = triage(task_id="t", static_findings=[first, second])
        assert len(result.reported) == 2, "distinct problem classes must both be reported"

    def test_dedup_key_stable_under_whitespace(self):
        first = _raw(evidence="db.execute( f'..{x}..' )")
        second = _raw(evidence="db.execute(  f'..{x}..'  )")
        result = triage(task_id="t", static_findings=[first, second])
        assert len(result.reported) == 1


class TestRedactionInTriage:

    def test_finding_evidence_redacted_before_persist(self):
        raw = _raw(category="secrets",
                   rule_id="SECRET003",
                   evidence='token = "ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"')
        result = triage(task_id="t", static_findings=[raw], redactor=Redactor())
        assert "ghp_a1B2c3D4" not in result.reported[0].evidence
