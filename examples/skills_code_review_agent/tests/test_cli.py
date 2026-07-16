# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI smoke tests (invoke main() in-process)."""
import json

import run_agent


async def test_cli_review_and_show(tmp_path, capsys):
    db_url = f"sqlite:///{tmp_path}/cr.db"
    code = await run_agent.main([
        "review", "--fixture", "security_eval", "--runtime", "local", "--dry-run",
        "--db-url", db_url, "--output-dir", str(tmp_path / "out")])
    assert code == 0
    out = capsys.readouterr().out
    assert "task_id=" in out
    task_id = out.split("task_id=")[1].split()[0].strip()

    code = await run_agent.main(["show", "--task-id", task_id, "--db-url", db_url])
    assert code == 0
    bundle = json.loads(capsys.readouterr().out)
    assert bundle["task"]["id"] == task_id
    assert bundle["findings"]


async def test_cli_show_unknown_task(tmp_path, capsys):
    db_url = f"sqlite:///{tmp_path}/cr.db"
    code = await run_agent.main(["show", "--task-id", "missing", "--db-url", db_url])
    assert code == 1
