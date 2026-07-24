# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for SandboxSession using the local workspace runtime."""
import os
from pathlib import Path

from review.sandbox import DIFF_WS_PATH, SandboxSession, create_runtime  # noqa: F401

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = EXAMPLE_ROOT / "skills" / "code-review"
FIXTURES = EXAMPLE_ROOT / "fixtures"


def _make_tmp_skill(tmp_path, script_name, body):
    skill = tmp_path / "code-review"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: code-review\ndescription: t\n---\nbody\n")
    (skill / "scripts" / script_name).write_text(body)
    return skill


async def _open_session(skill_root, **kw):
    runtime = await create_runtime("local")
    session = SandboxSession(runtime, str(skill_root), **kw)
    await session.open("cr_test_" + os.urandom(4).hex())
    return session


async def test_run_parse_diff_in_sandbox():
    session = await _open_session(SKILL_ROOT)
    try:
        await session.put_diff((FIXTURES / "clean.diff").read_text())
        outcome = await session.run_script("parse_diff.py")
        assert outcome.status == "ok", outcome.stderr
        assert '"files_changed": 2' in outcome.stdout
    finally:
        await session.close()


async def test_env_whitelist_blocks_host_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CR_LEAKED_SECRET", "should-not-be-visible")
    skill = _make_tmp_skill(tmp_path, "print_env.py",
                            "import json, os\nprint(json.dumps(dict(os.environ)))\n")
    session = await _open_session(skill)
    try:
        await session.put_diff("")
        outcome = await session.run_script("print_env.py", args=())
        assert outcome.status == "ok", outcome.stderr
        assert "CR_LEAKED_SECRET" not in outcome.stdout
    finally:
        await session.close()


async def test_timeout_is_enforced(tmp_path):
    skill = _make_tmp_skill(tmp_path, "sleepy.py", "import time\ntime.sleep(30)\n")
    session = await _open_session(skill, timeout_sec=2.0)
    try:
        await session.put_diff("")
        outcome = await session.run_script("sleepy.py", args=())
        assert outcome.timed_out is True
        assert outcome.status == "timeout"
    finally:
        await session.close()


async def test_output_size_cap(tmp_path):
    skill = _make_tmp_skill(tmp_path, "noisy.py", "print('x' * 1000000)\n")
    session = await _open_session(skill, max_output_bytes=1024)
    try:
        await session.put_diff("")
        outcome = await session.run_script("noisy.py", args=())
        assert len(outcome.stdout) <= 1024
        assert outcome.truncated is True
    finally:
        await session.close()


async def test_failing_script_reports_failed(tmp_path):
    skill = _make_tmp_skill(tmp_path, "broken.py", "import sys\nsys.exit(3)\n")
    session = await _open_session(skill)
    try:
        await session.put_diff("")
        outcome = await session.run_script("broken.py", args=())
        assert outcome.status == "failed"
        assert outcome.exit_code == 3
    finally:
        await session.close()
