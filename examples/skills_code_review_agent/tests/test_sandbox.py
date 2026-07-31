# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business tests for sandbox adaptation."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import examples.skills_code_review_agent.agent.sandbox as sandbox_module
import examples.skills_code_review_agent.agent.sandbox_workspace as workspace_module
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


def test_forbidden_referenced_input_path_is_denied_before_subprocess(tmp_path: Path, monkeypatch):
    allowed_dir = tmp_path / "allowed"
    denied_dir = tmp_path / "denied"
    allowed_dir.mkdir()
    denied_dir.mkdir()
    input_path, manifest_path = _write_inputs(denied_dir)
    output_path = allowed_dir / "rule_result.json"
    called = {"value": False}

    def fail_if_called(*args, **kwargs):
        called["value"] = True
        raise AssertionError("subprocess.run should not be called for forbidden paths")

    monkeypatch.setattr("examples.skills_code_review_agent.agent.sandbox.subprocess.run", fail_if_called)
    sandbox_run, events, result = run_rule_script(
        "task",
        RuntimeKind.LOCAL_DEV,
        True,
        input_path,
        manifest_path,
        output_path,
    )

    assert sandbox_run.status is SandboxStatus.DENIED
    assert events[0].reason_code.value == "forbidden_path"
    assert called["value"] is False
    assert result["findings"] == []


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


def test_cube_adapter_uses_one_event_loop_for_client_lifecycle(tmp_path: Path):
    input_path, manifest_path = _write_inputs(tmp_path)
    loop_ids: list[int] = []

    async def create_client():
        loop_ids.append(id(asyncio.get_running_loop()))
        return _LoopTrackingClient(loop_ids)

    def create_runtime(_client):
        loop_ids.append(id(asyncio.get_running_loop()))
        return _LoopTrackingWorkspaceRuntime(loop_ids)

    adapter = workspace_module._CubeWorkspaceRuntimeAdapter(
        RuntimeKind.CUBE,
        create_client,
        create_runtime,
    )

    async def invoke():
        return adapter.run_rule_script(
            "task",
            input_path,
            manifest_path,
            tmp_path / "rule_result.json",
            30,
            65536,
        )

    sandbox_run, result = asyncio.run(invoke())

    assert sandbox_run.status is SandboxStatus.SUCCESS
    assert result["schema_version"] == "code-review.rules.v1"
    assert len(set(loop_ids)) == 1


def test_workspace_cleanup_is_attempted_when_creation_fails(tmp_path: Path):
    input_path, manifest_path = _write_inputs(tmp_path)
    manager = _FailingCreateManager()
    runtime = _FakeWorkspaceRuntime()
    runtime.manager_obj = manager
    adapter = workspace_module._WorkspaceRuntimeAdapter(RuntimeKind.CONTAINER, runtime)

    sandbox_run, result = adapter.run_rule_script(
        "task",
        input_path,
        manifest_path,
        tmp_path / "rule_result.json",
        30,
        65536,
    )

    assert sandbox_run.status is SandboxStatus.FAILED
    assert sandbox_run.error_type == "RuntimeError"
    assert manager.cleanup_called is True
    assert result["findings"] == []


def test_workspace_bundle_is_importable_and_executable(tmp_path: Path):
    input_path, manifest_path = _write_inputs(tmp_path)
    bundle_root = tmp_path / "bundle"
    for file_info in workspace_module._workspace_bundle(input_path, manifest_path):
        target = bundle_root / file_info.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file_info.content)

    output_path = bundle_root / "work" / "outputs" / "rule_result.json"
    completed = subprocess.run(
        [
            sys.executable,
            "work/skills/code-review/scripts/rule_runner.py",
            "--input",
            "work/inputs/parsed_input.json",
            "--manifest",
            "work/inputs/skill_manifest.json",
            "--output",
            "work/outputs/rule_result.json",
        ],
        cwd=bundle_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert output_path.is_file()
    assert json.loads(output_path.read_text(encoding="utf-8"))["schema_version"] == "code-review.rules.v1"


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


class _FailingCreateManager:

    def __init__(self):
        self.cleanup_called = False

    async def create_workspace(self, exec_id):
        raise RuntimeError("workspace creation failed")

    async def cleanup(self, exec_id):
        self.cleanup_called = True


class _LoopTrackingClient:

    def __init__(self, loop_ids: list[int]):
        self.loop_ids = loop_ids

    async def destroy(self):
        self.loop_ids.append(id(asyncio.get_running_loop()))


class _LoopTrackingWorkspaceRuntime(_FakeWorkspaceRuntime):

    def __init__(self, loop_ids: list[int]):
        super().__init__()
        self.manager_obj = _LoopTrackingManager(loop_ids)


class _LoopTrackingManager(_FakeManager):

    def __init__(self, loop_ids: list[int]):
        self.loop_ids = loop_ids

    async def create_workspace(self, exec_id):
        self.loop_ids.append(id(asyncio.get_running_loop()))
        return await super().create_workspace(exec_id)

    async def cleanup(self, exec_id):
        self.loop_ids.append(id(asyncio.get_running_loop()))
        await super().cleanup(exec_id)


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
