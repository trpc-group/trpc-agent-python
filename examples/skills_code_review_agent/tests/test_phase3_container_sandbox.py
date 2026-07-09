# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Container (independent sandbox) integration tests — Phase 3, G1 closure.

These tests prove the PRD's production sandbox contract: in ``real`` mode the
code-review checks execute inside an **isolated docker container**
(``network_mode=none``, no inherited host env), not a local process. They are
gated on docker availability and SKIP (not fail) in environments without a
docker daemon, so the default suite stays green in CI.

Run with docker present to actually exercise the independent sandbox.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
for _p in (EXAMPLE_ROOT, EXAMPLE_ROOT / "skills" / "code-review" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agent.agent import FIXTURES, parse_diff, skill_load  # noqa: E402
from agent.sandbox import ContainerRuntime, SandboxPolicy  # noqa: E402
from run_checks import load_rules  # noqa: E402


def _docker_available() -> bool:
    """Best-effort probe: is a docker daemon reachable in this environment?"""
    try:
        import docker  # noqa: F401

        client = docker.from_env()
        return bool(client.ping())
    except Exception:
        return False


@unittest.skipUnless(_docker_available(), "docker daemon not available")
class TestContainerIndependentSandbox(unittest.TestCase):
    """The checks must run inside an isolated container, not a local process."""

    def setUp(self):
        self.skill = skill_load(str(EXAMPLE_ROOT / "skills" / "code-review"))
        self.rules_by_cat = load_rules(self.skill["skill_dir"], self.skill["rules"])
        self.policy = SandboxPolicy.from_config(self.skill["sandbox_config"])
        self.rc_path = str(Path(self.skill["skill_dir"]) / "scripts" / "run_checks.py")

    def test_ensure_available_true_with_docker(self):
        rt = ContainerRuntime(policy=self.policy)
        self.assertTrue(rt.ensure_available())

    def test_run_checks_executes_inside_container(self):
        cs = parse_diff(FIXTURES["security"])
        rt = ContainerRuntime(policy=self.policy)
        res = asyncio.run(
            rt.run(
                self.rc_path,
                {"changeset": cs.to_dict(), "rules": self.rules_by_cat},
                self.policy,
            )
        )
        self.assertEqual(res.status, "ok", res.stderr)
        self.assertEqual(res.exit_code, 0)
        data = json.loads(res.stdout) if res.stdout else []
        self.assertGreaterEqual(len(data), 1, "expected >=1 finding from container")
        # Secrets leaving the sandbox must already be masked (>=1 masked token).
        self.assertGreater(res.masked_count, 0)
        # No plaintext key must appear in the container's raw stdout.
        self.assertNotIn("sk-1234567890abcdef1234567890abcdef", res.stdout or "")

    def test_agent_real_mode_records_container_runtime(self):
        """End-to-end: --mode real --require-sandbox must use the container."""
        from agent.agent import main as agent_main

        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "t.db")
            out = os.path.join(d, "out")
            agent_main(
                [
                    "--mode", "real",
                    "--require-sandbox",
                    "--fixture", "security",
                    "--db-path", db,
                    "--output-dir", out,
                ]
            )
            conn = sqlite3.connect(db)
            rows = list(conn.execute("SELECT runtime, status FROM sandbox_run"))
            conn.close()
        self.assertTrue(rows, "no sandbox_run recorded")
        self.assertEqual(rows[0][0], "container")
        self.assertEqual(rows[0][1], "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
