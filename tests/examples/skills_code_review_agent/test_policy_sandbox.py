"""Tests for policy, redaction, and sandbox boundaries."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import stat

import pytest
from pydantic import ValidationError
from trpc_agent_sdk.code_executors import CodeFile
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import WorkspaceRunResult

from examples.skills_code_review_agent.agent.models import DecisionAction
from examples.skills_code_review_agent.agent.models import FilterDecision
from examples.skills_code_review_agent.agent.models import SandboxStatus
from examples.skills_code_review_agent.agent.policy import REDACTION_MARKER
from examples.skills_code_review_agent.agent.policy import ReviewPolicyFilter
from examples.skills_code_review_agent.agent.policy import SecretRedactor
from examples.skills_code_review_agent.agent.policy import build_execution_plan
from examples.skills_code_review_agent.agent.policy import calculate_plan_digest
from examples.skills_code_review_agent.agent.policy import run_guarded
from examples.skills_code_review_agent.agent import pipeline
from examples.skills_code_review_agent.agent.sandbox import RuntimeHandle
from examples.skills_code_review_agent.agent.sandbox import SandboxExecutor

SKILL_DIR = Path("examples/skills_code_review_agent/skills/code-review")
INPUT_BYTES = b'{"files":[]}'

SECRET_CASES = [
    'api_key="abcdefghijklmnop"',
    "password='correct-horse-battery'",
    'access_token="token-value-12345"',
    'client_secret="client-value-12345"',
    "Authorization: Bearer abcdefghijklmnop",
    "https://user:password123@example.invalid/path",
    "AK" + "IAABCDEFGHIJKLMNOP",
    "AI" + "zaABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
    "gh" + "p_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234",
    "gh" + "o_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234",
    "gh" + "u_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234",
    "s" + "k-abcdefghijklmnopqrstuvwxyz123456",
    "ey" + "Jabcdefghijk.eyJmnopqrstuv.abcdefghijklmnop",
    "-----BEGIN PRIVATE KEY-----\nsecret-body\n-----END PRIVATE KEY-----",
    'api_key = ("abcdefgh" + "ijklmnop")',
    '密码="这是一个很长的测试密码"',
    '密钥="unicode-secret-value"',
    '令牌="unicode-token-value"',
    'password="c2VjcmV0LXZhbHVlLTEyMzQ1"',
    "https://user:p%40ssword@example.invalid",
    '"password": "CorrectHorseBatteryStaple"',
]


class FakeManager:

    def __init__(self, delay: float = 0.0, cleanup_error: bool = False):
        self.delay = delay
        self.cleanup_error = cleanup_error
        self.cleanup_calls = 0

    async def create_workspace(self, exec_id):
        if self.delay:
            await asyncio.sleep(self.delay)
        return WorkspaceInfo(id=exec_id, path="/fake/workspace")

    async def cleanup(self, exec_id):
        del exec_id
        self.cleanup_calls += 1
        if self.cleanup_error:
            raise RuntimeError("cleanup failed")


class FakeFS:

    def __init__(self, output: str = ""):
        self.output = output
        self.put_paths = []

    async def put_files(self, workspace, files):
        del workspace
        self.put_paths = [item.path for item in files]

    async def collect(self, workspace, patterns):
        del workspace, patterns
        size = len(self.output.encode("utf-8"))
        return [
            CodeFile(
                name="out/findings.jsonl",
                content=self.output,
                mime_type="application/json",
                size_bytes=size,
            ),
        ]


class FakeRunner:

    def __init__(self, delay: float = 0.0, stdout: str = ""):
        self.delay = delay
        self.stdout = stdout
        self.calls = 0

    async def run_program(self, workspace, spec):
        del workspace, spec
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return WorkspaceRunResult(stdout=self.stdout, exit_code=0)


class FakeRuntime:

    def __init__(self, manager=None, filesystem=None, runner=None):
        self._manager = manager or FakeManager()
        self._filesystem = filesystem or FakeFS()
        self._runner = runner or FakeRunner()

    def manager(self):
        return self._manager

    def fs(self):
        return self._filesystem

    def runner(self):
        return self._runner


def _executor(runtime=None):
    fake = runtime or FakeRuntime()
    handle = RuntimeHandle(runtime=fake, kind="container", network_disabled=True)
    return SandboxExecutor(handle, SecretRedactor(), SKILL_DIR), fake


def _plan(executor, input_bytes=INPUT_BYTES):
    return build_execution_plan(
        "container",
        hashlib.sha256(input_bytes).hexdigest(),
        executor.skill_digest,
    )


def test_secret_redaction_recall_is_at_least_95_percent():
    redactor = SecretRedactor()
    detected = sum(REDACTION_MARKER in redactor.redact_text(case) for case in SECRET_CASES)

    assert detected / len(SECRET_CASES) >= 0.95


def test_structured_secret_values_are_redacted():
    redactor = SecretRedactor()
    payload = {
        "api_key": "short-but-sensitive",
        "nested": [{
            "password": "another-sensitive-value"
        }],
    }

    assert redactor.redact_value(payload) == {
        "api_key": REDACTION_MARKER,
        "nested": [{
            "password": REDACTION_MARKER
        }],
    }


def test_execution_plan_is_frozen():
    executor, _ = _executor()
    plan = _plan(executor)

    with pytest.raises(ValidationError):
        plan.argv = ("python", "other.py")


def test_workspace_is_writable_before_links_are_removed(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "code-review"
    skill_dir.mkdir(parents=True)
    skill_dir.chmod(skill_dir.stat().st_mode & ~stat.S_IWRITE)
    removed = []

    monkeypatch.setattr(
        pipeline,
        "_is_workspace_link",
        lambda path: path.name in pipeline.SKILL_WORKSPACE_LINK_NAMES,
    )

    def remove_link(path):
        assert skill_dir.stat().st_mode & stat.S_IWRITE
        removed.append(Path(path).name)

    monkeypatch.setattr(pipeline, "_remove_workspace_link", remove_link)

    pipeline._make_workspace_removable(tmp_path)

    assert removed == []

    for name in pipeline.SKILL_WORKSPACE_LINK_NAMES:
        (skill_dir / name).mkdir()
    pipeline._make_workspace_removable(tmp_path)

    assert removed == list(pipeline.SKILL_WORKSPACE_LINK_NAMES)


def test_filter_denies_posix_absolute_path_with_valid_digest():
    executor, _ = _executor()
    plan = _plan(executor).model_copy(update={
        "input_path": "/outside",
        "digest": "pending",
    }, )
    plan = plan.model_copy(update={"digest": calculate_plan_digest(plan)})

    decisions = ReviewPolicyFilter(lambda items: _noop()).evaluate(plan)

    path_decision = next(item for item in decisions if item.rule_id == "path.workspace")
    assert path_decision.action == DecisionAction.DENY


@pytest.mark.asyncio
async def test_denied_plan_never_calls_handler():
    executor, _ = _executor()
    plan = _plan(executor).model_copy(update={"argv": ("python", "other.py")}, )
    decisions = []
    handler_calls = 0

    async def audit(items):
        decisions.extend(items)

    async def handler(approved):
        nonlocal handler_calls
        del approved
        handler_calls += 1

    result, _ = await run_guarded(plan, ReviewPolicyFilter(audit), handler)

    assert result is None
    assert handler_calls == 0
    assert any(item.action == DecisionAction.DENY for item in decisions)


@pytest.mark.asyncio
async def test_needs_human_review_never_calls_handler(monkeypatch):
    executor, _ = _executor()
    plan = _plan(executor)
    handler_calls = 0
    decision = FilterDecision(
        action=DecisionAction.NEEDS_HUMAN_REVIEW,
        rule_id="test.human",
        reason="manual approval required",
        plan_digest=plan.digest,
        created_at="2026-01-01T00:00:00Z",
    )
    policy = ReviewPolicyFilter(lambda items: _noop())
    monkeypatch.setattr(policy, "evaluate", lambda value: [decision])

    async def handler(approved):
        nonlocal handler_calls
        del approved
        handler_calls += 1

    result, _ = await run_guarded(plan, policy, handler)

    assert result is None
    assert handler_calls == 0


async def _noop():
    return None


@pytest.mark.asyncio
async def test_filter_error_is_audited_as_deny(monkeypatch):
    executor, _ = _executor()
    plan = _plan(executor)
    audited = []
    policy = ReviewPolicyFilter(lambda items: _capture(audited, items))

    def fail(value):
        del value
        raise RuntimeError("filter failure")

    monkeypatch.setattr(policy, "evaluate", fail)
    decisions = await policy.evaluate_and_audit(plan)

    assert decisions[0].rule_id == "filter.internal-error"
    assert decisions[0].action == DecisionAction.DENY
    assert audited == decisions


async def _capture(target, values):
    target.extend(values)


@pytest.mark.asyncio
async def test_sandbox_stages_only_fixed_files_and_cleans_up():
    finding = ('{"category":"security","confidence":0.9,"evidence":"safe",'
               '"file":"app.py","line":1,"recommendation":"fix",'
               '"severity":"high","source":"test","title":"issue"}\n')
    executor, runtime = _executor(FakeRuntime(filesystem=FakeFS(finding)))
    result = await executor.execute(_plan(executor), INPUT_BYTES)

    assert result.run.status == SandboxStatus.SUCCEEDED
    assert result.output == finding
    assert runtime._manager.cleanup_calls == 1
    assert runtime._filesystem.put_paths == [
        "skills/code-review/SKILL.md",
        "skills/code-review/references/rules.md",
        "skills/code-review/scripts/scan_rules.py",
        "work/inputs/review_input.json",
    ]


@pytest.mark.asyncio
async def test_sandbox_timeout_cleans_up():
    runtime = FakeRuntime(runner=FakeRunner(delay=0.05))
    executor, runtime = _executor(runtime)
    plan = _plan(executor).model_copy(update={
        "timeout_seconds": 0.01,
        "output_limit_bytes": 10,
        "digest": "pending",
    }, )
    plan = plan.model_copy(update={"digest": calculate_plan_digest(plan)})

    result = await executor.execute(plan, INPUT_BYTES)

    assert result.run.status == SandboxStatus.TIMED_OUT
    assert result.run.timed_out is True
    assert runtime._manager.cleanup_calls == 1


@pytest.mark.asyncio
async def test_cleanup_failure_is_recorded_after_timeout():
    manager = FakeManager(cleanup_error=True)
    runtime = FakeRuntime(manager=manager, runner=FakeRunner(delay=0.05))
    executor, _ = _executor(runtime)
    plan = _plan(executor).model_copy(update={"timeout_seconds": 0.01, "digest": "pending"}, )
    plan = plan.model_copy(update={"digest": calculate_plan_digest(plan)})

    result = await executor.execute(plan, INPUT_BYTES)

    assert result.run.status == SandboxStatus.TIMED_OUT
    assert result.run.error_type == "TimeoutError+RuntimeError"
    output_size = len((result.run.stdout + result.run.stderr + result.output).encode("utf-8"), )
    assert output_size <= plan.output_limit_bytes


@pytest.mark.asyncio
async def test_sandbox_output_cap_is_enforced():
    output = "x" * 100
    executor, _ = _executor(FakeRuntime(filesystem=FakeFS(output)))
    plan = _plan(executor).model_copy(update={"output_limit_bytes": 10, "digest": "pending"}, )
    plan = plan.model_copy(update={"digest": calculate_plan_digest(plan)})

    result = await executor.execute(plan, INPUT_BYTES)

    assert result.run.status == SandboxStatus.FAILED
    assert result.run.error_type == "ValueError"


def test_local_runtime_requires_explicit_opt_in(monkeypatch):
    from examples.skills_code_review_agent.agent.sandbox import create_runtime

    monkeypatch.delenv("TRPC_CODE_REVIEW_ALLOW_UNSAFE_LOCAL", raising=False)
    with pytest.raises(PermissionError):
        create_runtime("local")


def test_container_runtime_explicitly_disables_network(monkeypatch):
    from examples.skills_code_review_agent.agent import sandbox

    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return FakeRuntime()

    monkeypatch.setattr(sandbox, "create_container_workspace_runtime", create)
    handle = sandbox.create_runtime("container")

    assert captured["host_config"]["network_mode"] == "none"
    assert handle.network_disabled is True
