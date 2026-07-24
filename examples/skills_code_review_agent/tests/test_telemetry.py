# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Telemetry acceptance tests.

Covers:
  1. init_telemetry (NoOp default + idempotent).
  2. trace_stage measures duration + forwards to recorder.
  3. trace_stage records exceptions (exception_types tally) + re-raises.
  4. TelemetryRecorder.to_monitor_summary shape.
  5. sandbox-tagged stages contribute to sandbox_duration_ms.
  6. agent --telemetry persists monitor_summary.

Run:
    C:/Users/douzhenyu/.workbuddy/binaries/python/envs/cr_agent/Scripts/python.exe \\
        examples/skills_code_review_agent/tests/test_telemetry.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_EXAMPLE_ROOT))

from agent.telemetry import TelemetryRecorder  # noqa: E402
from agent.telemetry import init_telemetry  # noqa: E402
from agent.telemetry import trace_stage  # noqa: E402


class TestTelemetryRecorder(unittest.TestCase):
    """DoD — recorder collects + folds into monitor_summary shape."""

    def test_to_monitor_summary_defaults(self):
        rec = TelemetryRecorder()
        s = rec.to_monitor_summary()
        self.assertEqual(s["total_duration_ms"], 0)
        self.assertEqual(s["sandbox_duration_ms"], 0)
        self.assertEqual(s["finding_count"], 0)
        self.assertEqual(s["exception_types"], {})
        for sev in ("sev_critical", "sev_high", "sev_medium", "sev_low"):
            self.assertEqual(s[sev], 0)

    def test_to_monitor_summary_with_findings_and_blocks(self):
        rec = TelemetryRecorder()
        rec.tool_calls = 3
        s = rec.to_monitor_summary(
            finding_count=5,
            sev_counts={"critical": 1, "high": 2, "medium": 1, "low": 1},
            blocks=2,
        )
        self.assertEqual(s["finding_count"], 5)
        self.assertEqual(s["sev_critical"], 1)
        self.assertEqual(s["sev_high"], 2)
        self.assertEqual(s["blocks"], 2)
        self.assertEqual(s["tool_calls"], 3)

    def test_sandbox_stage_contributes_to_sandbox_duration(self):
        rec = TelemetryRecorder()
        rec.record_stage("l1_parse", 100)
        rec.record_stage("l4_sandbox", 500)
        rec.record_stage("l4_sandbox_dedupe", 50)
        s = rec.to_monitor_summary()
        # total = 100 + 500 + 50 = 650; sandbox = 500 + 50 = 550
        self.assertEqual(s["total_duration_ms"], 650)
        self.assertEqual(s["sandbox_duration_ms"], 550)

    def test_exception_tally(self):
        rec = TelemetryRecorder()
        rec.record_exception("TimeoutError")
        rec.record_exception("TimeoutError")
        rec.record_exception("ValueError")
        self.assertEqual(rec.exceptions, {"TimeoutError": 2, "ValueError": 1})


class TestTraceStage(unittest.IsolatedAsyncioTestCase):
    """DoD — trace_stage measures + records + handles exceptions."""

    async def test_records_duration_to_recorder(self):
        rec = TelemetryRecorder()
        async with trace_stage("l1_parse", rec):
            await asyncio.sleep(0.01)
        self.assertIn("l1_parse", rec.stages)
        self.assertGreaterEqual(rec.stages["l1_parse"], 8)  # ~10ms

    async def test_works_without_init_noop(self):
        # Without init_telemetry the tracer is NoOp, but trace_stage still
        # measures + records on the recorder.
        rec = TelemetryRecorder()
        async with trace_stage("l2_skill_load", rec):
            pass
        self.assertIn("l2_skill_load", rec.stages)

    async def test_exception_recorded_and_reraised(self):
        rec = TelemetryRecorder()
        with self.assertRaises(RuntimeError):
            async with trace_stage("l4_sandbox", rec):
                raise RuntimeError("sandbox boom")
        self.assertEqual(rec.exceptions, {"RuntimeError": 1})
        # stage still recorded (finally ran)
        self.assertIn("l4_sandbox", rec.stages)

    async def test_multiple_stages_accumulate_total(self):
        rec = TelemetryRecorder()
        async with trace_stage("l1_parse", rec):
            await asyncio.sleep(0.005)
        async with trace_stage("l2_skill_load", rec):
            await asyncio.sleep(0.005)
        async with trace_stage("l6_persist", rec):
            await asyncio.sleep(0.005)
        s = rec.to_monitor_summary()
        self.assertGreater(s["total_duration_ms"], 10)
        self.assertEqual(len(rec.stages), 3)


class TestInitTelemetry(unittest.TestCase):
    """DoD — init is idempotent + safe."""

    def test_init_idempotent(self):
        # Calling twice must not raise (second call is a no-op).
        init_telemetry(enabled=False)
        init_telemetry(enabled=False)

    def test_init_disabled_is_noop(self):
        init_telemetry(enabled=False)
        # No exporter configured; tracer is default NoOp — trace_stage still works.


class TestAgentTelemetryIntegration(unittest.IsolatedAsyncioTestCase):
    """DoD — agent --telemetry persists monitor_summary to the DB."""

    async def test_agent_persists_monitor_summary(self):
        import agent
        db_path = tempfile.mktemp(suffix=".db", prefix="cr_tel_")
        out_dir = tempfile.mkdtemp(prefix="cr_tel_out_")
        try:
            # Run without --telemetry (NoOp spans) — monitor_summary still written.
            await agent._async_main(
                type("A", (), {
                    "skill_dir": str(_EXAMPLE_ROOT / "skills" / "code-review"),
                    "diff_file": None, "repo_path": None, "fixture": "security",
                    "db_path": db_path, "mode": "dry-run",
                    "print_changeset": False, "telemetry": False,
                    "output_dir": out_dir, "dry_run": False,
                })()
            )
            # Verify monitor_summary row exists with duration > 0.
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT total_duration_ms, sandbox_duration_ms, exception_types "
                "FROM monitor_summary"
            ).fetchone()
            conn.close()
            self.assertIsNotNone(row, "monitor_summary row must be written")
            self.assertGreater(row["total_duration_ms"], 0)
            self.assertEqual(row["exception_types"], "{}")
        finally:
            if os.path.exists(db_path):
                try:
                    os.unlink(db_path)
                except PermissionError:
                    pass
            shutil.rmtree(out_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
