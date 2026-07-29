# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Smoke tests for ``examples/skills_code_review_agent``.

Runs the full dry-run review pipeline (FakeModel drives skills staging,
filter governance, local sandbox execution and SQLite persistence) over two
shipped fixtures — no API key, no Docker, no third-party additions beyond
requirements-test.txt.

The example lives outside the package tree, so it is loaded by absolute path
(same pattern as test_optimize_quickstart_example.py).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "skills_code_review_agent"


@pytest.fixture(scope="module", autouse=True)
def _example_on_path():
    sys.path.insert(0, str(_EXAMPLE_DIR))
    yield
    sys.path.remove(str(_EXAMPLE_DIR))


def _run(fixture_name: str, tmp_path: Path):
    from review_agent.diff_parser import parse_diff_file
    from review_agent.pipeline import ReviewOptions, run_review

    fixture = _EXAMPLE_DIR / "fixtures" / fixture_name
    expected = json.loads((fixture / "expected.json").read_text(encoding="utf-8"))
    meta = expected.get("meta") or {}
    options = ReviewOptions(
        db_url=f"sqlite:///{tmp_path}/review.db",
        output_dir=str(tmp_path / "out"),
        unsafe_local=True,  # CI has no Docker; container mode is exercised in the example's own tests
        dry_run=True,
        run_timeout=int(meta.get("run_timeout", 60)),
        inject_sleep=float(meta.get("inject_sleep", 0)),
    )
    parsed = parse_diff_file(str(fixture / "input.diff"))
    parsed.input_type = "fixture"
    parsed.input_ref = fixture_name
    return asyncio.run(run_review(parsed, options))


def test_sql_injection_fixture_end_to_end(tmp_path):
    outcome = _run("02_sql_injection", tmp_path)
    assert outcome.status == "succeeded"
    security = [f for f in outcome.payload["findings"] if f["category"] == "security"]
    assert len(security) == 2
    assert all(f["severity"] == "critical" for f in security)
    assert Path(outcome.report_json_path).is_file()
    assert Path(outcome.report_md_path).is_file()


def test_secrets_fixture_redacts_and_persists(tmp_path):
    from review_agent.store import ReviewStore

    outcome = _run("08_secrets", tmp_path)
    assert outcome.status == "succeeded"
    assert len(outcome.payload["findings"]) >= 4
    report_text = Path(outcome.report_json_path).read_text(encoding="utf-8")
    assert "AKIAIOSFODNN7" not in report_text.replace("AKIAIOSFODNN7EXAMPLE", "")

    store = ReviewStore(f"sqlite:///{tmp_path}/review.db")

    async def load():
        await store.init()
        bundle = await store.load_task_bundle(outcome.task_id)
        await store.close()
        return bundle

    bundle = asyncio.run(load())
    assert bundle["task"].status == "succeeded"
    assert bundle["findings"] and bundle["sandbox_runs"] and bundle["metrics"]
