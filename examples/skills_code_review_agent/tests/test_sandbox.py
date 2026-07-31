# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business tests for sandbox adaptation."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from pathlib import Path

import examples.skills_code_review_agent.agent.sandbox as sandbox_module
from examples.skills_code_review_agent.agent import RuntimeKind
from examples.skills_code_review_agent.agent import SandboxStatus
from examples.skills_code_review_agent.agent import load_skill
from examples.skills_code_review_agent.agent import parse_unified_diff
from examples.skills_code_review_agent.agent import run_rule_script


def test_dry_run_writes_rule_result(tmp_path: Path):
    input_path, manifest_path = _write_inputs(tmp_path)
    output_path = tmp_path / "rule_result.json"

    sandbox_run, events, result = run_rule_script(
        "task",
        RuntimeKind.DRY_RUN,
        False,
        input_path,
        manifest_path,
        output_path,
    )

    assert sandbox_run.status is SandboxStatus.SUCCESS
    assert events[0].decision.value == "allow"
    assert output_path.is_file()
    assert result["schema_version"] == "code-review.rules.v1"


def test_local_dev_without_allow_is_denied(tmp_path: Path):
    input_path, manifest_path = _write_inputs(tmp_path)

    sandbox_run, events, result = run_rule_script(
        "task",
        RuntimeKind.LOCAL_DEV,
        False,
        input_path,
        manifest_path,
        tmp_path / "rule_result.json",
    )

    assert sandbox_run.status is SandboxStatus.DENIED
    assert events[0].reason_code.value == "local_runtime_denied"
    assert result["findings"] == []


def test_local_dev_with_allow_executes_rule_runner(tmp_path: Path):
    input_path, manifest_path = _write_inputs(tmp_path)

    sandbox_run, events, result = run_rule_script(
        "task",
        RuntimeKind.LOCAL_DEV,
        True,
        input_path,
        manifest_path,
        tmp_path / "rule_result.json",
    )

    assert sandbox_run.status is SandboxStatus.SUCCESS
    assert sandbox_run.exit_code == 0
    assert events[0].decision.value == "allow"
    assert result["skill_name"] == "code-review"


def test_container_runtime_records_human_review(tmp_path: Path, monkeypatch):
    input_path, manifest_path = _write_inputs(tmp_path)
    monkeypatch.setattr(
        "examples.skills_code_review_agent.agent.sandbox._resolve_runtime_adapter",
        lambda runtime, **kwargs: sandbox_module._RuntimeAdapterResult(
            runtime=runtime,
            available=False,
            reason="container runtime is unavailable",
        ),
    )

    sandbox_run, events, result = run_rule_script(
        "task",
        RuntimeKind.CONTAINER,
        False,
        input_path,
        manifest_path,
        tmp_path / "rule_result.json",
    )

    assert sandbox_run.status is SandboxStatus.NEEDS_HUMAN_REVIEW
    assert events[0].reason_code.value == "sandbox_unavailable"
    assert result["findings"] == []


def test_optional_runtime_adapter_success_is_recorded(tmp_path: Path, monkeypatch):
    input_path, manifest_path = _write_inputs(tmp_path)

    monkeypatch.setattr(
        "examples.skills_code_review_agent.agent.sandbox._resolve_runtime_adapter",
        lambda runtime, **kwargs: sandbox_module._RuntimeAdapterResult(
            runtime=runtime,
            available=True,
            adapter=sandbox_module._WorkspaceRuntimeAdapter(RuntimeKind.CONTAINER, _FakeWorkspaceRuntime()),
        ),
    )
    sandbox_run, events, result = run_rule_script(
        "task",
        RuntimeKind.CONTAINER,
        False,
        input_path,
        manifest_path,
        tmp_path / "rule_result.json",
    )

    assert sandbox_run.status is SandboxStatus.SUCCESS
    assert events[0].decision.value == "allow"
    assert result["skill_name"] == "code-review"


class _FakeWorkspaceRuntime:

    def __init__(self):
        self.fs_obj = _FakeFS()
        self.manager_obj = _FakeManager()
        self.runner_obj = _FakeRunner()

    def manager(self):
        return self.manager_obj

    def fs(self):
        return self.fs_obj

    def runner(self):
        return self.runner_obj


class _FakeManager:

    async def create_workspace(self, exec_id):
        return SimpleNamespace(id=exec_id, path="/workspace/ws")

    async def cleanup(self, exec_id):
        return None


class _FakeFS:

    async def put_files(self, workspace, files):
        assert any(file.path == "work/agent/models.py" for file in files)
        assert any(file.path == "work/skills/code-review/scripts/rule_runner.py" for file in files)

    async def collect_outputs(self, workspace, spec):
        assert spec.max_total_bytes > 0
        return SimpleNamespace(
            limits_hit=False,
            files=[
                SimpleNamespace(
                    name="work/outputs/rule_result.json",
                    content=json.dumps({
                        "schema_version": "code-review.rules.v1",
                        "skill_name": "code-review",
                        "findings": [],
                        "diagnostics": [],
                    }),
                )
            ],
        )


class _FakeRunner:

    async def run_program(self, workspace, spec):
        assert spec.cwd == "work"
        assert spec.timeout > 0
        assert spec.env["PYTHONPATH"] == "."
        return SimpleNamespace(stdout="", stderr="", exit_code=0, duration=0.01, timed_out=False)


def test_timeout_is_recorded_without_crashing(tmp_path: Path, monkeypatch):
    input_path, manifest_path = _write_inputs(tmp_path)

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["python"], timeout=1, output="password=secretvalue")

    monkeypatch.setattr("examples.skills_code_review_agent.agent.sandbox.subprocess.run", raise_timeout)
    sandbox_run, _events, result = run_rule_script(
        "task",
        RuntimeKind.LOCAL_DEV,
        True,
        input_path,
        manifest_path,
        tmp_path / "rule_result.json",
        timeout_sec=1,
    )

    assert sandbox_run.status is SandboxStatus.TIMEOUT
    assert "secretvalue" not in json.dumps(result)


def test_stdout_is_truncated_and_redacted(tmp_path: Path, monkeypatch):
    input_path, manifest_path = _write_inputs(tmp_path)

    class Completed:
        returncode = 0
        stdout = "password=secretvalue " + ("x" * 100)
        stderr = ""

    monkeypatch.setattr(
        "examples.skills_code_review_agent.agent.sandbox.subprocess.run",
        lambda *args, **kwargs: Completed(),
    )
    sandbox_run, _events, _result = run_rule_script(
        "task",
        RuntimeKind.LOCAL_DEV,
        True,
        input_path,
        manifest_path,
        tmp_path / "rule_result.json",
        output_limit_bytes=16,
    )

    assert sandbox_run.stdout_truncated is True
    assert "secretvalue" not in sandbox_run.stdout


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    summary = parse_unified_diff("", task_id="task")
    manifest = load_skill(Path(__file__).parents[1] / "skills" / "code-review").manifest
    input_path = tmp_path / "parsed_input.json"
    manifest_path = tmp_path / "skill_manifest.json"
    input_path.write_text(json.dumps(summary.to_dict()), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    return input_path, manifest_path
