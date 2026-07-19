"""Tests for safe and atomic Stage 5 report artifact publication."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from examples.optimization.eval_optimize_loop import artifact_writer
from examples.optimization.eval_optimize_loop import pipeline as pipeline_module
from examples.optimization.eval_optimize_loop.artifact_writer import (
    ArtifactWriteError,
    discover_run_artifacts,
    publish_report_bundle,
    write_failure_report,
)
from examples.optimization.eval_optimize_loop.pipeline import prepare_run
from examples.optimization.eval_optimize_loop.report_builder import (
    build_failure_report,
    build_optimization_report,
)
from examples.optimization.eval_optimize_loop.schemas import FailureReport, ReportProgress


_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _ROOT / "examples" / "optimization" / "eval_optimize_loop"


def _copy_example(tmp_path: Path) -> Path:
    target = tmp_path / "eval_optimize_loop"
    shutil.copytree(_EXAMPLE, target, ignore=shutil.ignore_patterns("runs", "__pycache__"))
    return target


async def _build_fake_report_for_root(root: Path, run_id: str):
    prepared = prepare_run(root / "pipeline.json", run_id=run_id)
    execution_progress = pipeline_module._MutableReportProgress(
        started_at=datetime(2026, 7, 18, tzinfo=timezone.utc)
    )
    result = await pipeline_module._execute_fake_stage(
        prepared,
        scenario="improve",
        progress=execution_progress,
    )
    progress = ReportProgress(
        started_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
        current_phase="reporting",
        completed_phases=[
            "baseline_train",
            "baseline_validation",
            "candidate_generation",
            "candidate_train",
            "candidate_validation",
            "analysis",
            "gate",
            "writeback",
        ],
    )
    report = build_optimization_report(
        prepared,
        result,
        progress=progress,
        finished_at=datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
    )
    return prepared, report


async def _build_fake_report(tmp_path: Path, run_id: str):
    return await _build_fake_report_for_root(_copy_example(tmp_path), run_id)


@pytest.mark.asyncio
async def test_publish_report_bundle_materializes_and_indexes_required_files(tmp_path):
    prepared, report = await _build_fake_report(tmp_path, "artifact_complete")
    run_dir = Path(prepared.workspace.run_dir)

    index = publish_report_bundle(report, run_dir=run_dir, copy_input_files=True)

    report_dir = run_dir / "report"
    assert (report_dir / "optimization_report.json").is_file()
    assert (report_dir / "optimization_report.md").is_file()
    assert (report_dir / "artifact_index.json").is_file()
    assert all(ref.relative_path != "report/artifact_index.json" for ref in index.artifacts)

    input_paths = {
        ref.artifact_id: ref.relative_path
        for ref in index.artifacts
        if ref.artifact_type == "input"
    }
    assert input_paths == {
        "input.pipeline_config": "report/inputs/pipeline_config.json",
        "input.optimizer_config": "report/inputs/optimizer_config.json",
        "input.train_evalset": "report/inputs/train_evalset.json",
        "input.validation_evalset": "report/inputs/validation_evalset.json",
    }
    assert {
        ref.artifact_id: ref.relative_path
        for ref in index.artifacts
        if ref.artifact_type == "prompt"
    } == {
        "prompt.baseline.system_prompt": (
            "report/prompts/baseline/000-system_prompt.md"
        ),
        "prompt.candidate.system_prompt": (
            "report/prompts/candidate/000-system_prompt.md"
        ),
    }
    baseline_prompt = report_dir / "prompts" / "baseline" / "000-system_prompt.md"
    candidate_prompt = report_dir / "prompts" / "candidate" / "000-system_prompt.md"
    assert baseline_prompt.read_text(encoding="utf-8") == (
        report.input_snapshot.prompt_snapshots[0].content
    )
    assert candidate_prompt.read_text(encoding="utf-8") == (
        report.candidate.prompts["system_prompt"]
    )
    evaluation_paths = {
        ref.artifact_id: ref.relative_path
        for ref in index.artifacts
        if ref.artifact_type == "evaluation"
    }
    assert evaluation_paths == {
        f"evaluation.{name}": f"report/evaluations/{name}.json"
        for name in (
            "baseline_train",
            "baseline_validation",
            "candidate_train",
            "candidate_validation",
        )
    }
    assert {
        path.name for path in (report_dir / "evaluations").iterdir()
    } == {
        "baseline_train.json",
        "baseline_validation.json",
        "candidate_train.json",
        "candidate_validation.json",
    }

    available = [ref for ref in index.artifacts if ref.status == "available"]
    assert available
    for ref in available:
        assert ref.relative_path is not None
        relative_path = Path(ref.relative_path)
        assert not relative_path.is_absolute()
        assert ".." not in relative_path.parts
        path = run_dir / relative_path
        assert path.is_file()
        assert ref.sha256 == sha256(path.read_bytes()).hexdigest()
        assert ref.size_bytes == path.stat().st_size


@pytest.mark.asyncio
async def test_publish_rejects_input_hash_drift(tmp_path):
    prepared, report = await _build_fake_report(tmp_path, "artifact_drift")
    train_path = Path(prepared.input_snapshot.train_evalset_path)
    train_path.write_text(
        train_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    run_dir = Path(prepared.workspace.run_dir)

    with pytest.raises(ArtifactWriteError, match="input hash"):
        publish_report_bundle(report, run_dir=run_dir, copy_input_files=True)

    assert not (run_dir / "report").exists()
    assert list(run_dir.glob(".report.tmp-*")) == []


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "api_key",
        "access_token",
        "client_secret",
        "password",
        "x-api-key",
        "openai_api_key",
        "provider_api_key",
        "endpoint_url",
    ],
)
@pytest.mark.asyncio
async def test_publish_rejects_plaintext_secret_in_optimizer_config_without_leaking_it(
    tmp_path, sensitive_key
):
    root = _copy_example(tmp_path)
    optimizer_path = root / "optimizer.json"
    payload = json.loads(optimizer_path.read_text(encoding="utf-8"))
    secret = "sk-real-sentinel-secret-must-not-be-copied"
    payload["optimize"]["algorithm"]["reflection_lm"]["extra_fields"] = {
        sensitive_key: secret
    }
    optimizer_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    prepared, report = await _build_fake_report_for_root(root, "artifact_secret")
    run_dir = Path(prepared.workspace.run_dir)

    with pytest.raises(ArtifactWriteError, match="sensitive optimizer config"):
        publish_report_bundle(report, run_dir=run_dir, copy_input_files=True)

    assert secret in optimizer_path.read_text(encoding="utf-8")
    assert not (run_dir / "report").exists()
    assert list(run_dir.glob(".report.tmp-*")) == []
    secret_bytes = secret.encode("utf-8")
    assert all(
        secret_bytes not in path.read_bytes()
        for path in run_dir.rglob("*")
        if path.is_file()
    )


@pytest.mark.asyncio
async def test_publish_rejects_literal_url_hidden_under_provider_specific_key(
    tmp_path,
):
    root = _copy_example(tmp_path)
    optimizer_path = root / "optimizer.json"
    payload = json.loads(optimizer_path.read_text(encoding="utf-8"))
    endpoint = "https://private-provider.example.test/v1"
    payload["optimize"]["algorithm"]["reflection_lm"]["extra_fields"] = {
        "service_location": endpoint
    }
    optimizer_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    prepared, report = await _build_fake_report_for_root(root, "artifact_url")
    run_dir = Path(prepared.workspace.run_dir)

    with pytest.raises(ArtifactWriteError, match="sensitive optimizer config"):
        publish_report_bundle(report, run_dir=run_dir, copy_input_files=True)

    assert not (run_dir / "report").exists()
    endpoint_bytes = endpoint.encode("utf-8")
    assert all(
        endpoint_bytes not in path.read_bytes()
        for path in run_dir.rglob("*")
        if path.is_file()
    )


@pytest.mark.asyncio
async def test_publish_rejects_existing_report_directory(tmp_path):
    prepared, report = await _build_fake_report(tmp_path, "artifact_existing")
    report_dir = Path(prepared.workspace.run_dir) / "report"
    report_dir.mkdir()

    with pytest.raises(ArtifactWriteError, match="already exists"):
        publish_report_bundle(
            report,
            run_dir=Path(prepared.workspace.run_dir),
            copy_input_files=True,
        )


@pytest.mark.asyncio
async def test_publish_does_not_replace_report_directory_created_during_staging(
    tmp_path, monkeypatch
):
    prepared, report = await _build_fake_report(tmp_path, "artifact_report_race")
    run_dir = Path(prepared.workspace.run_dir)
    report_dir = run_dir / "report"
    original_write = artifact_writer._write_text

    def create_competing_report_before_publish(path, content):
        original_write(path, content)
        if path.name == "artifact_index.json":
            report_dir.mkdir()

    monkeypatch.setattr(
        artifact_writer,
        "_write_text",
        create_competing_report_before_publish,
    )

    with pytest.raises(ArtifactWriteError, match="already exists"):
        publish_report_bundle(report, run_dir=run_dir, copy_input_files=True)

    assert report_dir.is_dir()
    assert list(report_dir.iterdir()) == []
    assert list(run_dir.glob(".report.tmp-*")) == []


def test_publish_fails_closed_when_atomic_no_replace_is_unsupported(
    tmp_path, monkeypatch
):
    source = tmp_path / "staging"
    target = tmp_path / "report"
    source.mkdir()

    def unexpected_rename(self, destination):
        raise AssertionError("普通 Path.rename 不得作为 no-replace 回退")

    monkeypatch.setattr(artifact_writer.sys, "platform", "freebsd")
    monkeypatch.setattr(Path, "rename", unexpected_rename)

    with pytest.raises(ArtifactWriteError, match="atomic no-replace unavailable"):
        artifact_writer._rename_directory_no_replace(source, target)

    assert source.is_dir()
    assert not target.exists()


@pytest.mark.parametrize("target_inside_run", [False, True])
def test_discovery_rejects_any_file_symlink(tmp_path, target_inside_run):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    target_root = run_dir if target_inside_run else tmp_path
    target = target_root / "target.txt"
    target.write_text("target", encoding="utf-8")
    (run_dir / "link.txt").symlink_to(target)

    with pytest.raises(ArtifactWriteError, match="symbolic link"):
        discover_run_artifacts(run_dir)


def test_discovery_rejects_directory_symlink_without_following_it(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "artifact.txt").write_text("outside", encoding="utf-8")
    (run_dir / "linked-directory").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArtifactWriteError, match="symbolic link"):
        discover_run_artifacts(run_dir)


@pytest.mark.asyncio
async def test_publish_rejects_symlink_in_optimizer_artifacts(tmp_path):
    prepared, report = await _build_fake_report(tmp_path, "artifact_optimizer_link")
    run_dir = Path(prepared.workspace.run_dir)
    optimizer_dir = run_dir / "optimizer"
    optimizer_dir.mkdir()
    outside = tmp_path / "optimizer-result.json"
    outside.write_text("{}\n", encoding="utf-8")
    (optimizer_dir / "result.json").symlink_to(outside)

    with pytest.raises(ArtifactWriteError, match="symbolic link"):
        publish_report_bundle(report, run_dir=run_dir, copy_input_files=True)

    assert not (run_dir / "report").exists()
    assert list(run_dir.glob(".report.tmp-*")) == []


@pytest.mark.asyncio
async def test_publish_failure_removes_staging_directory(tmp_path, monkeypatch):
    prepared, report = await _build_fake_report(tmp_path, "artifact_partial")
    calls = 0
    original = artifact_writer._write_text

    def fail_second_write(path, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated write failure")
        original(path, content)

    monkeypatch.setattr(artifact_writer, "_write_text", fail_second_write)
    run_dir = Path(prepared.workspace.run_dir)

    with pytest.raises(ArtifactWriteError, match="simulated write failure"):
        publish_report_bundle(report, run_dir=run_dir, copy_input_files=True)

    assert not (run_dir / "report").exists()
    assert list(run_dir.glob(".report.tmp-*")) == []


@pytest.mark.asyncio
async def test_failure_report_is_atomic_and_round_trips(tmp_path):
    prepared, _ = await _build_fake_report(tmp_path, "artifact_failure")
    progress = ReportProgress(
        started_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
        current_phase="analysis",
        completed_phases=[
            "baseline_train",
            "baseline_validation",
            "candidate_generation",
            "candidate_train",
            "candidate_validation",
        ],
    )
    failure = build_failure_report(
        prepared,
        progress=progress,
        error=RuntimeError("simulated analysis failure"),
        source_prompt_hashes={
            snapshot.field_name: snapshot.sha256
            for snapshot in prepared.input_snapshot.prompt_snapshots
        },
        existing_artifacts=[],
        generated_at=datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
    )

    path = write_failure_report(failure, run_dir=Path(prepared.workspace.run_dir))

    assert FailureReport.model_validate_json(path.read_text(encoding="utf-8")) == failure
    assert list(path.parent.glob(".failure_report.tmp-*")) == []


@pytest.mark.asyncio
async def test_failure_report_rejects_existing_first_failure_evidence(tmp_path):
    prepared, _ = await _build_fake_report(tmp_path, "artifact_failure_existing")
    run_dir = Path(prepared.workspace.run_dir)
    target = run_dir / "failure_report.json"
    target.write_text('{"first": true}\n', encoding="utf-8")
    original = target.read_bytes()
    progress = ReportProgress(
        started_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
        current_phase="reporting",
        completed_phases=[],
    )
    failure = build_failure_report(
        prepared,
        progress=progress,
        error=RuntimeError("second failure"),
        source_prompt_hashes={},
        existing_artifacts=[],
        generated_at=datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(ArtifactWriteError, match="already exists"):
        write_failure_report(failure, run_dir=run_dir)

    assert target.read_bytes() == original
    assert list(run_dir.glob(".failure_report.tmp-*")) == []


@pytest.mark.asyncio
async def test_failure_report_does_not_overwrite_target_created_during_write(
    tmp_path, monkeypatch
):
    prepared, _ = await _build_fake_report(tmp_path, "artifact_failure_race")
    run_dir = Path(prepared.workspace.run_dir)
    target = run_dir / "failure_report.json"
    first_failure = b'{"first": true}\n'
    original_write = artifact_writer._write_text

    def create_first_failure_after_temporary_write(path, content):
        original_write(path, content)
        target.write_bytes(first_failure)

    monkeypatch.setattr(
        artifact_writer,
        "_write_text",
        create_first_failure_after_temporary_write,
    )
    progress = ReportProgress(
        started_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
        current_phase="reporting",
        completed_phases=[],
    )
    failure = build_failure_report(
        prepared,
        progress=progress,
        error=RuntimeError("racing failure"),
        source_prompt_hashes={},
        existing_artifacts=[],
        generated_at=datetime(2026, 7, 18, 0, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(ArtifactWriteError, match="already exists"):
        write_failure_report(failure, run_dir=run_dir)

    assert target.read_bytes() == first_failure
    assert list(run_dir.glob(".failure_report.tmp-*")) == []


@pytest.mark.asyncio
async def test_copy_input_files_false_records_unavailable_inputs(tmp_path):
    prepared, report = await _build_fake_report(tmp_path, "artifact_no_inputs")

    index = publish_report_bundle(
        report,
        run_dir=Path(prepared.workspace.run_dir),
        copy_input_files=False,
    )

    input_refs = [
        ref for ref in index.artifacts if ref.artifact_id.startswith("input.")
    ]
    assert input_refs
    assert {ref.status for ref in input_refs} == {"unavailable"}
    assert all(
        ref.unavailable_reason == "artifacts.copy_input_files=false"
        for ref in input_refs
    )
