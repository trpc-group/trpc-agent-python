"""Issue #92 acceptance tests over public fixtures and full fake Agent flow."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import runpy
import time

from mccabe import get_code_complexity
import pytest

from examples.skills_code_review_agent.agent.input_parser import load_diff_file
from examples.skills_code_review_agent.agent.models import SandboxStatus
from examples.skills_code_review_agent.agent.models import SandboxRun
from examples.skills_code_review_agent.agent.models import TaskStatus
from examples.skills_code_review_agent.agent.pipeline import FAKE_MODEL_NAME
from examples.skills_code_review_agent.agent.pipeline import FakeReviewModel
from examples.skills_code_review_agent.agent.pipeline import PipelineDependencies
from examples.skills_code_review_agent.agent.pipeline import ReviewPipeline
from examples.skills_code_review_agent.agent.policy import SecretRedactor
from examples.skills_code_review_agent.agent.sandbox import create_runtime
from examples.skills_code_review_agent.agent.sandbox import SandboxExecution
from examples.skills_code_review_agent.agent.sandbox import SandboxExecutor
from examples.skills_code_review_agent.agent.storage import ReviewStore

EXAMPLE_ROOT = Path("examples/skills_code_review_agent")
FIXTURE_ROOT = EXAMPLE_ROOT / "fixtures"
SKILL_ROOT = EXAMPLE_ROOT / "skills"
SCANNER_PATH = SKILL_ROOT / "code-review/scripts/scan_rules.py"
EXPECTED_FIXTURE_COUNT = 8
MIN_DETECTION_RATE = 0.80
MAX_FALSE_POSITIVE_RATE = 0.15
MAX_FAKE_SECONDS = 120
MAX_FILE_LINES = 1_000
MAX_FUNCTION_LINES = 80
MAX_FUNCTION_STATEMENTS = 60
MAX_FUNCTION_PARAMETERS = 4
MAX_COMPLEXITY = 15
SUCCESS_FIXTURES = (
    "async_resource",
    "clean",
    "db_lifecycle",
    "duplicate",
    "missing_tests",
    "secrets",
    "security",
)


def _load_scanner():
    return runpy.run_path(str(SCANNER_PATH))["scan"]


def _key(item: dict) -> tuple[str, str, int | None]:
    return item["category"], item["file"], item["line"]


def test_public_fixtures_meet_detection_and_false_positive_targets() -> None:
    scanner = _load_scanner()
    expected_files = sorted(FIXTURE_ROOT.glob("*.expected.json"))
    assert len(expected_files) == EXPECTED_FIXTURE_COUNT
    expected_total = detected = false_positives = actual_total = 0
    for expected_path in expected_files:
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        expected_keys = {_key(item) for item in expected["expected_findings"]}
        if expected["expected_sandbox_status"] == SandboxStatus.TIMED_OUT.value:
            assert not expected_keys
            continue
        diff_name = expected_path.name.replace(".expected.json", ".diff")
        review_input = load_diff_file(expected_path.with_name(diff_name))
        actual_keys = {_key(item) for item in scanner(review_input.model_dump(mode="json"))}
        expected_total += len(expected_keys)
        actual_total += len(actual_keys)
        detected += len(expected_keys & actual_keys)
        false_positives += len(actual_keys - expected_keys)
        assert len(actual_keys - expected_keys) <= expected["max_false_positives"]
    assert detected / expected_total >= MIN_DETECTION_RATE
    denominator = max(actual_total, 1)
    assert false_positives / denominator <= MAX_FALSE_POSITIVE_RATE


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_name", SUCCESS_FIXTURES)
async def test_fake_agent_runs_skill_filter_sandbox_db_and_report(
    fixture_name,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TRPC_CODE_REVIEW_ALLOW_UNSAFE_LOCAL", "1")
    store = ReviewStore(
        f"sqlite:///{(tmp_path / 'review.db').as_posix()}",
        SecretRedactor(),
    )
    await store.initialize()
    started = time.monotonic()
    try:
        dependencies = PipelineDependencies(
            store=store,
            runtime=create_runtime("local", tmp_path / "work"),
            skill_root=SKILL_ROOT,
            output_dir=tmp_path / "reports",
            model=FakeReviewModel(model_name=FAKE_MODEL_NAME),
        )
        report, json_path, markdown_path = await ReviewPipeline(dependencies).run(
            load_diff_file(FIXTURE_ROOT / f"{fixture_name}.diff"), )
        persisted = await store.get_report(report.task_id)
    finally:
        await store.close()
    assert report.status == TaskStatus.COMPLETE, report.failures
    assert report.metrics.tool_calls == 2
    expected = json.loads((FIXTURE_ROOT / f"{fixture_name}.expected.json").read_text(encoding="utf-8"), )
    actual_keys = {_key(item.model_dump(mode="json")) for item in report.findings}
    expected_keys = {_key(item) for item in expected["expected_findings"]}
    assert len(report.findings) == len(actual_keys)
    assert actual_keys == expected_keys
    if fixture_name == "duplicate":
        security = next(item for item in report.findings if item.category.value == "security")
        assert security.source == "skill:code-review/security.shell-true"
    assert report.filter_decisions[0].action.value == "allow"
    assert report.sandbox_runs[0].status == SandboxStatus.SUCCEEDED
    assert persisted["report"]["task_id"] == report.task_id
    assert json_path.is_file() and markdown_path.is_file()
    assert time.monotonic() - started < MAX_FAKE_SECONDS
    assert not list((tmp_path / "work").glob("ws_*"))


@pytest.mark.asyncio
async def test_sandbox_failure_fixture_runs_full_recovery_path(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TRPC_CODE_REVIEW_ALLOW_UNSAFE_LOCAL", "1")

    async def timeout(executor, plan, input_bytes):
        del executor, plan, input_bytes
        return SandboxExecution(
            run=SandboxRun(
                status=SandboxStatus.TIMED_OUT,
                timed_out=True,
                duration_ms=1,
                error_type="TimeoutError",
            ),
            output="",
        )

    monkeypatch.setattr(SandboxExecutor, "execute", timeout)
    store = ReviewStore(
        f"sqlite:///{(tmp_path / 'failure.db').as_posix()}",
        SecretRedactor(),
    )
    await store.initialize()
    try:
        dependencies = PipelineDependencies(
            store=store,
            runtime=create_runtime("local", tmp_path / "work"),
            skill_root=SKILL_ROOT,
            output_dir=tmp_path / "reports",
            model=FakeReviewModel(model_name=FAKE_MODEL_NAME),
        )
        report, _, _ = await ReviewPipeline(dependencies).run(load_diff_file(FIXTURE_ROOT / "sandbox_failure.diff"), )
    finally:
        await store.close()
    assert report.status == TaskStatus.FAILED
    assert report.sandbox_runs[0].status == SandboxStatus.TIMED_OUT
    assert report.metrics.exceptions_by_type == {"TimeoutError": 1}
    assert not report.findings


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("TRPC_CODE_REVIEW_CONTAINER_TEST") != "1",
    reason="set TRPC_CODE_REVIEW_CONTAINER_TEST=1 to run Docker integration",
)
async def test_container_pipeline_when_enabled(tmp_path) -> None:
    store = ReviewStore(
        f"sqlite:///{(tmp_path / 'container.db').as_posix()}",
        SecretRedactor(),
    )
    await store.initialize()
    try:
        dependencies = PipelineDependencies(
            store=store,
            runtime=create_runtime("container"),
            skill_root=SKILL_ROOT,
            output_dir=tmp_path / "reports",
            model=FakeReviewModel(model_name=FAKE_MODEL_NAME),
        )
        report, _, _ = await ReviewPipeline(dependencies).run(load_diff_file(FIXTURE_ROOT / "security.diff"), )
    finally:
        await store.close()
    assert report.status == TaskStatus.COMPLETE


def test_python_source_meets_static_limits() -> None:
    sources = [*EXAMPLE_ROOT.rglob("*.py"), *Path(__file__).parent.glob("*.py")]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert len(text.splitlines()) <= MAX_FILE_LINES, path
        tree = ast.parse(text)
        assert get_code_complexity(text, MAX_COMPLEXITY, str(path)) == 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= MAX_FUNCTION_LINES, (
                    path,
                    node.name,
                )
                statements = sum(isinstance(item, ast.stmt) for item in ast.walk(node))
                assert statements <= MAX_FUNCTION_STATEMENTS, (path, node.name)
                arguments = [
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                ]
                assert len(arguments) <= MAX_FUNCTION_PARAMETERS, (path, node.name)
