# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end pipeline over ALL 8 committed fixtures (acceptance criterion 1)."""

import os
import time

import pytest

from .helpers import findings_by_category
from .helpers import run_fixture

ALL_FIXTURES = [
    "clean",
    "security_issue",
    "async_resource_leak",
    "db_connection_lifecycle",
    "missing_tests",
    "duplicate_finding",
    "sandbox_failure",
    "secret_redaction",
]

REPORT_KEYS = ("task_id", "created_at", "input", "status", "summary", "findings",
               "needs_human_review", "severity_stats", "filter_summary",
               "sandbox_summary", "metrics", "recommendations")

METRIC_KEYS = ("total_duration_ms", "sandbox_duration_ms", "sandbox_run_count",
               "tool_call_count", "llm_call_count", "filter_block_count",
               "filter_decisions", "finding_count", "needs_human_review_count",
               "deduplicated_count", "redaction_count", "severity_distribution",
               "error_types")


@pytest.mark.parametrize("fixture_name", ALL_FIXTURES)
async def test_every_fixture_runs_and_reports(fixture_name, tmp_path):
    run = await run_fixture(fixture_name, tmp_path,
                            force_fail=(fixture_name == "sandbox_failure"))
    try:
        # report files rendered
        assert os.path.isfile(run.result.report_paths["json"])
        assert os.path.isfile(run.result.report_paths["markdown"])
        # full document shape
        for key in REPORT_KEYS:
            assert key in run.report, key
        for key in METRIC_KEYS:
            assert key in run.report["metrics"], key
        # DB is complete and queryable by task id (acceptance criterion 3)
        bundle = await run.store.get_task_bundle(run.result.task_id)
        assert bundle["task"]["status"] in ("completed", "completed_with_errors")
        assert bundle["report"] is not None
        assert len(bundle["sandbox_runs"]) >= 1
        db_findings = [row for row in bundle["findings"] if row["bucket"] == "finding"]
        assert len(db_findings) == len(run.report["findings"])
        # every finding carries the full required schema
        for finding in run.report["findings"]:
            for key in ("severity", "category", "file", "line", "title", "evidence",
                        "recommendation", "confidence", "source"):
                assert key in finding, (fixture_name, key)
            assert finding["recommendation"].strip()
    finally:
        await run.store.close()


async def test_clean_fixture_yields_zero_findings(tmp_path):
    run = await run_fixture("clean", tmp_path)
    try:
        assert run.report["findings"] == []
        assert run.report["needs_human_review"] == []
        assert run.result.status == "completed"
    finally:
        await run.store.close()


async def test_security_fixture_detects_high_risk(tmp_path):
    run = await run_fixture("security_issue", tmp_path)
    try:
        categories = findings_by_category(run.report)
        security = categories.get("security_risk", [])
        assert any(finding["severity"] in ("critical", "high") for finding in security)
        rule_ids = {finding.get("rule_id") for finding in security}
        assert "SEC006" in rule_ids  # f-string SQL injection
    finally:
        await run.store.close()


async def test_async_resource_leak_fixture(tmp_path):
    run = await run_fixture("async_resource_leak", tmp_path)
    try:
        all_items = run.report["findings"] + run.report["needs_human_review"]
        categories = {item["category"] for item in all_items}
        assert "async_error" in categories
        assert "resource_leak" in categories
        # high-confidence async finding stays in findings
        assert any(item["category"] == "async_error" for item in run.report["findings"])
        # the open()-without-with heuristic (conf 0.65) must sit in human review
        assert any(item["category"] == "resource_leak" and item["rule_id"] == "RES001"
                   for item in run.report["needs_human_review"])
    finally:
        await run.store.close()


async def test_db_lifecycle_fixture(tmp_path):
    run = await run_fixture("db_connection_lifecycle", tmp_path)
    try:
        db_findings = findings_by_category(run.report).get("db_lifecycle", [])
        rule_ids = {finding["rule_id"] for finding in db_findings}
        assert "DBL001" in rule_ids  # connect without close
        assert "DBL003" in rule_ids  # BEGIN without commit/rollback
    finally:
        await run.store.close()


async def test_missing_tests_fixture(tmp_path):
    run = await run_fixture("missing_tests", tmp_path)
    try:
        assert findings_by_category(run.report).get("missing_tests")
    finally:
        await run.store.close()


async def test_duplicate_finding_fixture_dedups(tmp_path):
    run = await run_fixture("duplicate_finding", tmp_path)
    try:
        # os.system("..." + var) triggers SEC001 AND SEC009 on the same line —
        # exactly one survives (same file+line+category), the merge is recorded.
        security = findings_by_category(run.report).get("security_risk", [])
        assert len(security) == 1
        assert run.report["metrics"]["deduplicated_count"] >= 1
        keys = [(finding["file"], finding["line"], finding["category"])
                for finding in run.report["findings"]]
        assert len(keys) == len(set(keys))
    finally:
        await run.store.close()


async def test_sandbox_failure_fixture_survives(tmp_path):
    run = await run_fixture("sandbox_failure", tmp_path, force_fail=True)
    try:
        assert run.result.status == "completed_with_errors"
        runs = await run.store.get_sandbox_runs(run.result.task_id)
        assert runs[0]["status"] == "failed"
        assert runs[0]["error_type"] == "SandboxNonZeroExit"
        # host fallback still produced a report (fixture is clean → 0 findings)
        assert os.path.isfile(run.result.report_paths["json"])
        assert run.report["sandbox_summary"]["runs"][0]["status"] == "failed"
        assert run.report["metrics"]["error_types"].get("SandboxNonZeroExit") == 1
    finally:
        await run.store.close()


async def test_secret_redaction_fixture_no_plaintext_anywhere(tmp_path):
    run = await run_fixture("secret_redaction", tmp_path)
    try:
        secrets = findings_by_category(run.report).get("secret_leakage", [])
        assert len(secrets) >= 15  # ≥20 seeded, ≥15 distinct (file,line) after dedup

        seeded_values = [
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY99",
            "ghp_FAKE1234567890abcdefFAKE1234567890",
            "xoxb-7777777-8888888-FAKEfakeFAKE",
            "sk-FAKEfakeFAKEfakeFAKEfakeFAKE1234",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlLXVzZXIifQ",
            "FAKEbearerTOKENfakeBEARER1234567890",
            "sup3rSecretDbPass",
            "hunter2butlonger99",
            "topSecretValue4242",
            "plainTokenValue31337",
            "clientSecretValue0004",
            "dbPasswordValue0006",
            "MIIFAKEKEYBODYnotARealKeyMIIFAKE",
        ]
        # neither the JSON report, the MD report nor the raw DB file may
        # contain any seeded plaintext value (acceptance criterion 5)
        report_text = str(run.report) + run.report_md
        with open(run.db_path, "rb") as fh:
            db_bytes = fh.read()
        for value in seeded_values:
            assert value not in report_text, f"secret leaked into report: {value}"
            assert value.encode() not in db_bytes, f"secret leaked into DB: {value}"
        assert run.report["metrics"]["redaction_count"] >= 0  # counter exposed
    finally:
        await run.store.close()


async def test_dry_run_speed_and_no_api_key(tmp_path, monkeypatch):
    """Acceptance criterion 6: full offline run without any API key in ≤2 min."""
    for var in ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME",
                "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    started = time.perf_counter()
    run = await run_fixture("security_issue", tmp_path, model_mode="fake")
    try:
        elapsed = time.perf_counter() - started
        assert elapsed < 120, f"dry-run took {elapsed:.1f}s"
        assert run.result.status == "completed"
        assert run.report["summary"]  # fake model produced a real summary
        assert run.report["metrics"]["llm_call_count"] == 1
    finally:
        await run.store.close()
