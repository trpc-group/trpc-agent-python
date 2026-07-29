#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Integration tests for SDK workspace sandbox factory, staging and limits."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from trpc_agent_sdk.code_executors import CodeFile
from trpc_agent_sdk.code_executors import ManifestFileRef
from trpc_agent_sdk.code_executors import ManifestOutput
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import WorkspaceRunResult


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from code_review.config import ReviewConfig  # noqa: E402
from code_review.inputs import FixturePayload  # noqa: E402
from code_review.pipeline import ReviewPipeline  # noqa: E402
from code_review.redaction import contains_plaintext_secret  # noqa: E402
from code_review.sandbox import (  # noqa: E402
    SandboxBudget,
    SandboxBudgetExceeded,
    SandboxConfigurationError,
    SandboxStageError,
    SandboxRuntimeSelection,
    SanitizedLocalProgramRunner,
    SdkSkillSandbox,
    StagedSkill,
    bounded_output,
    build_run_spec,
    build_output_spec,
    build_sandbox_environment,
    capture_workspace_run,
    change_set_payload,
    create_sandbox_runtime,
    stage_code_review_skill,
)
from code_review.store import SqlReviewStore  # noqa: E402
from run_agent import PipelineGovernance  # noqa: E402

SCRIPTS_ROOT = PROJECT_ROOT / "skills" / "code-review" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from lib.diff_parser import parse_unified_diff  # noqa: E402
from parse_diff import _load_change_set  # noqa: E402


SKILL_ROOT = PROJECT_ROOT / "skills" / "code-review"
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "diffs"


class _FakeManager:
    """提供本任务 staging 测试所需的最小 SDK workspace manager 替身。"""

    async def create_workspace(self, execution_id: str, _ctx: Any = None) -> WorkspaceInfo:
        """返回固定 workspace，避免测试创建真实容器或本机临时目录。"""

        return WorkspaceInfo(id=execution_id, path="/sandbox/workspace")

    async def cleanup(self, _execution_id: str, _ctx: Any = None) -> None:
        """模拟 SDK 清理接口，当前 staging 场景不产生真实资源。"""


class _FakeFs:
    """记录 SDK staging 调用，并模拟受控脚本内容的收集结果。"""

    def __init__(
        self,
        *,
        drift: bool = False,
        drift_path: str = "run_checks.py",
    ) -> None:
        """读取可信脚本内容，并可选择追加摘要漂移以覆盖失败关闭路径。"""

        self._drift = drift
        self._drift_path = drift_path
        self.stage_calls: list[tuple[str, str, Any]] = []
        self.put_files_calls = 0

    async def stage_directory(self, _ws: WorkspaceInfo, src: str, dst: str, options: Any, _ctx: Any = None) -> None:
        """记录 SDK 目录复制参数，供只读、copy 和最小目标路径断言。"""

        self.stage_calls.append((src, dst, options))

    async def collect(self, _ws: WorkspaceInfo, patterns: list[str], _ctx: Any = None) -> list[CodeFile]:
        """为 workspace 元数据和 run_checks 脚本返回最小可验证内容。"""

        if patterns == [".trpc_workspace.json"]:
            return []
        if len(patterns) == 1 and patterns[0].startswith(
            "skills/code-review/scripts/"
        ):
            relative_path = patterns[0].removeprefix(
                "skills/code-review/scripts/"
            )
            source_path = SKILL_ROOT / "scripts" / relative_path
            if not source_path.is_file():
                return []
            content = source_path.read_text(encoding="utf-8")
            if self._drift and relative_path == self._drift_path:
                content += "\n# drift\n"
            return [
                CodeFile(
                    name=patterns[0],
                    content=content,
                    mime_type="text/x-python",
                )
            ]
        return []

    async def put_files(self, _ws: WorkspaceInfo, _files: list[Any], _ctx: Any = None) -> None:
        """接收 SDK stager 的元数据写入，但不把数据落到宿主文件系统。"""

        self.put_files_calls += 1


class _FakeRunner:
    """响应 SDK stager 的只读辅助命令，不执行任意命令文本。"""

    def __init__(self) -> None:
        """保存 SDK stager 发起的辅助运行规格，供只读权限断言使用。"""

        self.specifications: list[Any] = []

    async def run_program(self, _ws: WorkspaceInfo, _spec: Any, _ctx: Any = None) -> WorkspaceRunResult:
        """返回成功结果，让测试仅聚焦 stage 配置和摘要复验。"""

        self.specifications.append(_spec)
        return WorkspaceRunResult(exit_code=0)


class _FakeRuntime:
    """提供具备 SDK 方法形状的 fake runtime，避免依赖 Docker 或 Cube。"""

    def __init__(self, fs: _FakeFs) -> None:
        """保存 fake filesystem、manager 和 runner 以供 repository/stager 协作调用。"""

        self._fs = fs
        self._manager = _FakeManager()
        self._runner = _FakeRunner()

    def fs(self, _ctx: Any = None) -> _FakeFs:
        """返回记录 staging 行为的 fake filesystem。"""

        return self._fs

    def manager(self, _ctx: Any = None) -> _FakeManager:
        """返回最小 workspace manager。"""

        return self._manager

    def runner(self, _ctx: Any = None) -> _FakeRunner:
        """返回仅支持 stager 辅助操作的 fake runner。"""

        return self._runner


class _RunFs(_FakeFs):
    """扩展 fake filesystem，以受控 findings 输出或输出截断模拟实际 collect_outputs。"""

    def __init__(self, *, limits_hit: bool = False) -> None:
        """保存是否触发 SDK 输出收集上限，并复用可信 staged 脚本内容。"""

        super().__init__()
        self._limits_hit = limits_hit

    async def collect_outputs(self, _ws: WorkspaceInfo, _spec: Any, _ctx: Any = None) -> ManifestOutput:
        """返回最小 findings JSON 或 limits_hit，避免测试执行真实规则脚本。"""

        return ManifestOutput(
            files=[ManifestFileRef(name="out/findings.json", content='{"findings": []}')],
            limits_hit=self._limits_hit,
        )


class _RunRuntime(_FakeRuntime):
    """允许为 SDK 沙箱端口注入 timeout 或 nonzero 的一次运行结果。"""

    def __init__(self, fs: _RunFs, result: WorkspaceRunResult) -> None:
        """替换基础 fake runner 的返回值，以驱动失败即数据分支。"""

        super().__init__(fs)
        self._run_result = result

    def runner(self, _ctx: Any = None) -> Any:
        """返回带预设运行结果的最小 runner，仍支持 stager 的辅助调用。"""

        parent_runner = super().runner()
        run_result = self._run_result

        class _Runner:
            """在保留 stager 调用记录的同时返回测试指定的主执行结果。"""

            async def run_program(self, ws: WorkspaceInfo, spec: Any, ctx: Any = None) -> WorkspaceRunResult:
                """对 bash stager 辅助命令成功返回，对固定 python3 命令返回预设结果。"""

                if spec.cmd == "bash":
                    return await parent_runner.run_program(ws, spec, ctx)
                return run_result

        return _Runner()


def test_container_factory_defaults_to_verified_none_and_rejects_mount_or_override() -> None:
    """容器工厂默认强制 network_mode=none，拒绝覆盖和宿主 bind 挂载。"""

    observed: dict[str, object] = {}

    def _container_factory(*, host_config: dict[str, object]) -> object:
        """捕获工厂传入的最终 host config，不连接 Docker daemon。"""

        observed.update(host_config)
        return object()

    selection = create_sandbox_runtime(
        "container",
        container_runtime_factory=_container_factory,
    )

    assert selection.runtime_type == "container"
    assert selection.effective_network_mode == "none"
    assert selection.network_policy_verified is True
    assert observed == {"network_mode": "none"}
    with pytest.raises(SandboxConfigurationError, match="network_mode"):
        create_sandbox_runtime("container", host_config={"network_mode": "bridge"})
    with pytest.raises(SandboxConfigurationError, match="host_mount"):
        create_sandbox_runtime("container", host_config={"Binds": ["host:container:rw"]})


def test_stage_uses_sdk_skill_repository_copy_and_revalidates_digest() -> None:
    """Skill 必须通过 SDK repository/stager 复制到固定目录并在 staging 后复验摘要。"""

    filesystem = _FakeFs()
    runtime = _FakeRuntime(filesystem)
    staged = asyncio.run(
        stage_code_review_skill(
            runtime,
            WorkspaceInfo(id="task-stage", path="/sandbox/workspace"),
            SKILL_ROOT,
        )
    )

    assert staged.workspace_skill_dir == "skills/code-review"
    assert staged.entrypoint == "skills/code-review/scripts/run_checks.py"
    assert len(filesystem.stage_calls) == 1
    source, destination, options = filesystem.stage_calls[0]
    assert Path(source).resolve() == SKILL_ROOT.resolve()
    assert destination == "skills/code-review"
    assert options.mode == "copy"
    assert options.read_only is False
    assert options.allow_mount is False
    assert filesystem.put_files_calls >= 1
    assert any("chmod a-w" in " ".join(spec.args) for spec in runtime.runner().specifications)

    with pytest.raises(SandboxStageError, match="staged_script_integrity_mismatch"):
        asyncio.run(
            stage_code_review_skill(
                _FakeRuntime(_FakeFs(drift=True)),
                WorkspaceInfo(id="task-drift", path="/sandbox/workspace"),
                SKILL_ROOT,
            )
        )
    with pytest.raises(SandboxStageError, match="staged_script_integrity_mismatch"):
        asyncio.run(
            stage_code_review_skill(
                _FakeRuntime(
                    _FakeFs(
                        drift=True,
                        drift_path="lib/rules_security.py",
                    )
                ),
                WorkspaceInfo(
                    id="task-dependency-drift",
                    path="/sandbox/workspace",
                ),
                SKILL_ROOT,
            )
        )


def test_budget_output_and_environment_limits_are_preflighted() -> None:
    """预算、输出收集和环境变量均在执行前按锁定上限和白名单收敛。"""

    config = ReviewConfig()
    budget = SandboxBudget(config)
    reservation = budget.reserve(timeout_seconds=30, output_bytes=1024)
    output = bounded_output("x" * 20, "", max_bytes=8)
    environment = build_sandbox_environment()
    output_spec = build_output_spec(config)

    assert reservation.run_number == 1
    assert output.truncated is True
    assert len(output.stdout.encode("utf-8")) <= 8
    assert output_spec.max_files == 1
    assert output_spec.max_file_bytes == config.max_output_bytes_per_run
    assert output_spec.max_total_bytes == config.max_output_bytes_per_run
    assert set(environment) == {"LANG", "LC_ALL", "PYTHONUNBUFFERED"}
    assert all("CANARY" not in name for name in environment)
    with pytest.raises(SandboxBudgetExceeded, match="sandbox_run_budget_exceeded"):
        for _ in range(config.max_sandbox_runs):
            budget.reserve(timeout_seconds=1, output_bytes=1)

    time_budget = SandboxBudget(config)
    for _ in range(3):
        time_budget.reserve(timeout_seconds=30, output_bytes=1)
    with pytest.raises(SandboxBudgetExceeded, match="sandbox_time_budget_exceeded"):
        time_budget.reserve(timeout_seconds=1, output_bytes=1)

    output_budget = SandboxBudget(config)
    output_budget.reserve(timeout_seconds=1, output_bytes=config.max_output_bytes_per_run)
    output_budget.reserve(timeout_seconds=1, output_bytes=config.max_output_bytes_per_run)
    with pytest.raises(SandboxBudgetExceeded, match="sandbox_review_output_budget_exceeded"):
        output_budget.reserve(timeout_seconds=1, output_bytes=1)


def test_run_spec_and_timeout_capture_remain_bounded() -> None:
    """固定 argv 必须携带每次超时；超时结果与超限输出应在宿主边界被收敛。"""

    config = ReviewConfig()
    secret = "ghp_" + "a" * 36
    run_spec = build_run_spec(
        StagedSkill(
            workspace_skill_dir="skills/code-review",
            entrypoint="skills/code-review/scripts/run_checks.py",
            script_id="run_checks",
            sha256="a" * 64,
        ),
        config,
    )
    capture = capture_workspace_run(
        WorkspaceRunResult(
            stdout=secret + "y" * 20,
            stderr="",
            exit_code=0,
            duration=0.031,
            timed_out=True,
        ),
        max_output_bytes=32,
    )

    assert run_spec.cmd == "python3"
    assert run_spec.args == ["scripts/run_checks.py"]
    local_run_spec = build_run_spec(
        StagedSkill(
            workspace_skill_dir="skills/code-review",
            entrypoint="skills/code-review/scripts/run_checks.py",
            script_id="run_checks",
            sha256="a" * 64,
        ),
        config,
        python_executable="python",
        use_workspace_root=True,
    )
    assert local_run_spec.args == ["skills/code-review/scripts/run_checks.py"]
    assert local_run_spec.cwd == "."
    assert run_spec.timeout == config.per_run_timeout_seconds
    assert set(run_spec.env) == {"LANG", "LC_ALL", "PYTHONUNBUFFERED"}
    assert capture.timed_out is True
    assert capture.output.truncated is True
    assert capture.duration_ms == 31
    assert secret not in capture.output.stdout


def test_local_program_runner_removes_host_environment_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 local fallback 子进程只收到白名单和 SDK workspace 变量。"""

    monkeypatch.setenv(
        "TRPC_AGENT_API_KEY",
        "synthetic-canary-that-must-not-enter-the-child",
    )
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    runner = SanitizedLocalProgramRunner()
    environment = runner._build_program_env(
        WorkspaceInfo(id="local-environment", path=str(workspace_path)),
        WorkspaceRunProgramSpec(
            cmd=sys.executable,
            args=("-c", "pass"),
            env=build_sandbox_environment(),
        ),
    )

    assert "TRPC_AGENT_API_KEY" not in environment
    assert set(build_sandbox_environment()) <= set(environment)
    assert all(
        "synthetic-canary" not in value
        for value in environment.values()
    )


def test_sandbox_budget_is_scoped_to_each_review_task() -> None:
    """验证复用同一 Agent 沙箱时，不同 task 不会共享累计预算。"""

    runtime = _RunRuntime(_RunFs(), WorkspaceRunResult(exit_code=0))
    sandbox = SdkSkillSandbox(
        SandboxRuntimeSelection(runtime, "local", None, False, True),
        SKILL_ROOT,
    )
    change_set = parse_unified_diff(
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
        source_kind="fixture",
    )

    for index in range(4):
        task_id = f"independent-review-{index}"
        result = sandbox.execute(
            task_id=task_id,
            change_set=change_set,
            config=ReviewConfig(),
        )
        sandbox.cleanup(task_id=task_id)
        assert result["status"] == "ok"


def test_sandbox_payload_preserves_repo_full_file_ast_context(
    tmp_path: Path,
) -> None:
    """验证 repo 增量输入进入沙箱后仍保留完整文件供 AST 规则分析。"""

    parsed = parse_unified_diff(
        "diff --git a/src/service.py b/src/service.py\n"
        "--- a/src/service.py\n"
        "+++ b/src/service.py\n"
        "@@ -2 +2 @@\n"
        "-    return command\n"
        "+    return eval(command)\n",
        source_kind="repo_path",
    )
    full_text = "def execute(command):\n    return eval(command)\n"
    change_set = replace(
        parsed,
        files=(
            replace(
                parsed.files[0],
                full_text=full_text,
                analysis_mode="ast_validated",
            ),
        ),
    )
    input_path = tmp_path / "diff.json"
    input_path.write_bytes(change_set_payload(change_set))

    restored = _load_change_set(input_path)

    assert restored.source_kind == "repo_path"
    assert restored.files[0].full_text == full_text
    assert restored.files[0].analysis_mode == "ast_validated"


@pytest.mark.parametrize(
    ("run_result", "limits_hit", "expected_status", "expected_error"),
    (
        (WorkspaceRunResult(exit_code=0, timed_out=True), False, "timeout", "timeout"),
        (WorkspaceRunResult(exit_code=9), False, "failed", "nonzero_exit"),
        (WorkspaceRunResult(exit_code=0), True, "error", "output_truncated"),
    ),
)
def test_sdk_sandbox_converts_runtime_failures_to_structured_data(
    run_result: WorkspaceRunResult,
    limits_hit: bool,
    expected_status: str,
    expected_error: str,
) -> None:
    """timeout、非零和输出截断都应返回脱敏结构化结果，而不是让评审链路崩溃。"""

    runtime = _RunRuntime(_RunFs(limits_hit=limits_hit), run_result)
    sandbox = SdkSkillSandbox(
        SandboxRuntimeSelection(runtime, "local", None, False, True),
        SKILL_ROOT,
    )
    change_set = parse_unified_diff(
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n",
        source_kind="fixture",
    )

    result = sandbox.execute(task_id="sandbox-failure", change_set=change_set, config=ReviewConfig())

    assert result["status"] == expected_status
    assert result["error_type"] == expected_error
    assert result["findings"] == []


def _docker_daemon_available() -> bool:
    """仅探测 Docker daemon 可用性；该测试辅助函数不创建容器、镜像或网络。"""

    executable = shutil.which("docker")
    if executable is None:
        return False
    try:
        result = subprocess.run(
            [executable, "version", "--format", "{{.Server.Version}}"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _container_db_url(path: Path) -> str:
    """构造真实 container 验收专用的临时 SQLite URL，不写入业务数据库。"""

    return f"sqlite+pysqlite:///{path.as_posix()}"


@pytest.mark.container
@pytest.mark.parametrize(
    "fixture_name",
    ("02_security_simple", "08_secret_redaction_simple"),
)
def test_container_executes_fixture_with_verified_network_none(
    fixture_name: str,
    tmp_path: Path,
) -> None:
    """在可用 Docker daemon 上执行两条真实 fixture，并验证容器实际网络模式为 none。"""

    if not _docker_daemon_available():
        pytest.skip("container_runtime_unavailable")

    config = ReviewConfig()
    selection = create_sandbox_runtime("container")
    sandbox = SdkSkillSandbox(selection, SKILL_ROOT, config=config)
    store = SqlReviewStore(_container_db_url(tmp_path / "container-review.db"))
    pipeline = ReviewPipeline(
        store=store,
        governance=PipelineGovernance(
            selection=selection,
            config=config,
            workspace_root=tmp_path / "governance-workspace",
        ),
        sandbox=sandbox,
        output_dir=tmp_path / "reports",
        config=config,
        task_id_factory=lambda: f"container-{fixture_name}",
    )
    container_client = selection.runtime.manager(None).container
    try:
        result = pipeline.run(
            fixture=FixturePayload(
                payload_type="diff",
                diff_text=(FIXTURE_DIR / f"{fixture_name}.diff").read_text(encoding="utf-8"),
            )
        )
        bundle = store.get_task_bundle(result.task_id)
        container = container_client.container

        assert container is not None
        assert container.attrs["HostConfig"]["NetworkMode"] == "none"
        assert bundle is not None
        assert bundle["sandbox_runs"][0]["status"] == "ok"
        assert result.json_path.is_file()
        assert result.markdown_path.is_file()
        assert not contains_plaintext_secret(json.dumps(bundle, ensure_ascii=False, sort_keys=True))
    finally:
        store.close()
        container_client._cleanup_container()
