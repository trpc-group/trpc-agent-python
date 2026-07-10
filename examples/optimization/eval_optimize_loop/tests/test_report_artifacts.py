from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from examples.optimization.eval_optimize_loop.eval_loop import pipeline as pipeline_module
from examples.optimization.eval_optimize_loop.eval_loop import report as report_module
from examples.optimization.eval_optimize_loop.eval_loop.pipeline import execute_pipeline
from examples.optimization.eval_optimize_loop.eval_loop.schemas import CostSummary
from examples.optimization.eval_optimize_loop.eval_loop.schemas import OptimizationRound
from examples.optimization.eval_optimize_loop.tests.test_pipeline_orchestration import (
    FailingCandidateBackend,
)
from examples.optimization.eval_optimize_loop.tests.test_pipeline_orchestration import (
    RecordingBackend,
)
from examples.optimization.eval_optimize_loop.tests.test_pipeline_orchestration import _request
from examples.optimization.eval_optimize_loop.tests.test_pipeline_orchestration import _safe_backend


class _BaselineCrashBackend(RecordingBackend):
    async def evaluate(self, **kwargs: Any):
        artifact_dir = Path(kwargs["artifact_dir"])
        artifact_dir.joinpath("backend-started.txt").write_text("started\n", encoding="utf-8")
        if kwargs["prompt_id"] == "baseline" and kwargs["split"] == "train":
            raise RuntimeError("simulated backend crash")
        return await super().evaluate(**kwargs)


def _crashing_backend() -> _BaselineCrashBackend:
    delegate = _safe_backend(candidate_count=1)
    return _BaselineCrashBackend(
        candidates=delegate.candidates,
        results=delegate.results,
        optimization_cost=delegate.optimization_cost,
    )


def _temp_run_dir(request: Any) -> Path:
    return Path(request.output_dir) / "runs" / f".{request.run_id}.tmp"


def _final_run_dir(request: Any) -> Path:
    return Path(request.output_dir) / "runs" / request.run_id


def _round(round_id: int, *, rationale: str) -> OptimizationRound:
    return OptimizationRound(
        round_id=round_id,
        candidate_id="candidate_a",
        prompts={"system_prompt": "safe prompt"},
        rationale=rationale,
        metrics={"train_score": 1.0},
        cost=CostSummary(),
        duration_seconds=0.01,
    )


def _assert_manifest_records_match_files(run_dir: Path, manifest: dict[str, Any]) -> None:
    seen: set[str] = set()
    for record in manifest["files"]:
        relative_path = record["path"]
        assert relative_path not in seen
        seen.add(relative_path)
        content = (run_dir / relative_path).read_bytes()
        assert record["sha256"] == hashlib.sha256(content).hexdigest(), relative_path
        assert record["size_bytes"] == len(content), relative_path


def _create_directory_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except (NotImplementedError, OSError) as symlink_error:
        pytest.skip(f"directory symlinks unavailable: {symlink_error}")


def _remove_directory_link(link: Path) -> None:
    if link.is_symlink():
        link.unlink()
    elif os.path.lexists(link):
        os.rmdir(link)


async def _source_report(tmp_path: Path, label: str):
    source_root = tmp_path / label
    source_root.mkdir()
    request = _request(source_root)
    backend = _safe_backend(candidate_count=1)
    return await execute_pipeline(request, evaluator=backend, optimizer=backend)


class _FakeRenameAt2:
    def __init__(self, result: int) -> None:
        self.result = result
        self.calls: list[tuple[Any, ...]] = []
        self.argtypes: list[Any] | None = None
        self.restype: Any = None

    def __call__(self, *args: Any) -> int:
        self.calls.append(args)
        return self.result


def _install_fake_renameat2(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: int,
    error_number: int = 0,
) -> _FakeRenameAt2:
    real_ctypes = report_module.ctypes
    function = _FakeRenameAt2(result)

    class _FakeLibc:
        renameat2 = function

    class _CtypesProxy:
        def CDLL(self, name: Any, *, use_errno: bool):
            assert name is None
            assert use_errno is True
            return _FakeLibc()

        def get_errno(self) -> int:
            return error_number

        def __getattr__(self, name: str):
            return getattr(real_ctypes, name)

    monkeypatch.setattr(report_module, "ctypes", _CtypesProxy())
    return function


def test_posix_rename_noreplace_calls_linux_abi_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    function = _install_fake_renameat2(monkeypatch, result=0)
    source = tmp_path / "source"
    target = tmp_path / "target"

    report_module._posix_rename_noreplace(source, target)

    assert function.argtypes == [
        report_module.ctypes.c_int,
        report_module.ctypes.c_char_p,
        report_module.ctypes.c_int,
        report_module.ctypes.c_char_p,
        report_module.ctypes.c_uint,
    ]
    assert function.restype is report_module.ctypes.c_int
    assert function.calls == [
        (-100, os.fsencode(source), -100, os.fsencode(target), 0x1),
    ]


def test_posix_rename_noreplace_maps_collision_to_file_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_renameat2(
        monkeypatch,
        result=-1,
        error_number=report_module.errno.EEXIST,
    )
    target = tmp_path / "target"

    with pytest.raises(FileExistsError) as caught:
        report_module._posix_rename_noreplace(tmp_path / "source", target)

    assert caught.value.errno == report_module.errno.EEXIST
    assert caught.value.filename == str(target)


@pytest.mark.parametrize("failure", ["missing_symbol", "unsupported_kernel"])
def test_posix_rename_noreplace_fails_closed_when_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
):
    real_ctypes = report_module.ctypes
    if failure == "missing_symbol":
        class _LibcWithoutRenameAt2:
            pass

        class _MissingSymbolProxy:
            def CDLL(self, name: Any, *, use_errno: bool):
                return _LibcWithoutRenameAt2()

            def __getattr__(self, name: str):
                return getattr(real_ctypes, name)

        monkeypatch.setattr(report_module, "ctypes", _MissingSymbolProxy())
        expected_errno = report_module.errno.ENOTSUP
    else:
        _install_fake_renameat2(
            monkeypatch,
            result=-1,
            error_number=report_module.errno.ENOSYS,
        )
        expected_errno = report_module.errno.ENOSYS

    with pytest.raises(OSError) as caught:
        report_module._posix_rename_noreplace(tmp_path / "source", tmp_path / "target")

    assert not isinstance(caught.value, FileExistsError)
    assert caught.value.errno == expected_errno


def test_posix_publish_race_never_replaces_new_final_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    paths = report_module.reserve_run_artifacts(tmp_path / "out", run_id="racing_run")
    evidence = paths.temp_run_dir / "evidence.txt"
    evidence.write_bytes(b"reserved temp evidence")
    raced_identity: dict[str, int] = {}

    def race_at_atomic_noreplace(source: Path, target: Path) -> None:
        assert Path(source) == paths.temp_run_dir
        assert Path(target) == paths.final_run_dir
        assert not target.exists()
        target.mkdir()
        raced_identity["inode"] = target.stat().st_ino
        raise FileExistsError(17, "final appeared during atomic publish", str(target))

    real_os = report_module.os

    class _PosixOSProxy:
        name = "posix"

        def __getattr__(self, name: str):
            return getattr(real_os, name)

    monkeypatch.setattr(report_module, "os", _PosixOSProxy())
    monkeypatch.setattr(report_module, "_fsync_directory", lambda directory: None)
    monkeypatch.setattr(
        report_module,
        "_posix_rename_noreplace",
        race_at_atomic_noreplace,
        raising=False,
    )

    with pytest.raises(FileExistsError, match="final appeared|already published"):
        report_module._durable_publish_directory(paths)

    assert paths.final_run_dir.is_dir()
    assert paths.final_run_dir.stat().st_ino == raced_identity["inode"]
    assert paths.temp_run_dir.is_dir()
    assert evidence.read_bytes() == b"reserved temp evidence"


@pytest.mark.asyncio
@pytest.mark.parametrize("reserved_name", ["ordered_run", ".ordered_run.tmp"])
async def test_duplicate_run_id_never_overwrites_final_or_reuses_temp(
    tmp_path: Path,
    reserved_name: str,
):
    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)
    occupied = Path(request.output_dir) / "runs" / reserved_name
    occupied.mkdir(parents=True)
    sentinel = occupied / "sentinel.txt"
    sentinel.write_bytes(b"keep me exactly")

    with pytest.raises(FileExistsError, match=request.run_id):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert sentinel.read_bytes() == b"keep me exactly"
    assert backend.calls == []
    assert Path(request.target_prompt_paths["system_prompt"]).read_bytes() == b"baseline\n"
    assert list(tmp_path.glob(".*.snapshot-*")) == []


@pytest.mark.asyncio
async def test_pipeline_rejects_duplicate_round_ids_before_report_artifact_writes(tmp_path: Path):
    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)
    backend.rounds = [_round(1, rationale="first"), _round(1, rationale="duplicate")]

    with pytest.raises(ValueError, match=r"duplicate round_id.*1"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    temp_run = _temp_run_dir(request)
    assert temp_run.is_dir()
    assert not _final_run_dir(request).exists()
    for report_artifact in (
        "pre_write_report.json",
        "artifact_manifest.json",
        "rounds.json",
        "rounds",
    ):
        assert not (temp_run / report_artifact).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", ["prepare", "write_reports"])
async def test_direct_report_writers_reject_duplicate_round_ids_before_writing(
    tmp_path: Path,
    writer: str,
):
    source_root = tmp_path / f"source-{writer}"
    source_root.mkdir()
    source_request = _request(source_root)
    source_report = await execute_pipeline(
        source_request,
        evaluator=_safe_backend(candidate_count=1),
        optimizer=_safe_backend(candidate_count=1),
    )
    run_id = f"duplicate_rounds_{writer}"
    report = replace(
        source_report,
        run={**source_report.run, "run_id": run_id},
        rounds=[_round(7, rationale="first"), _round(7, rationale="duplicate")],
    )
    output_dir = tmp_path / f"direct-{writer}"

    if writer == "prepare":
        paths = report_module.reserve_run_artifacts(output_dir, run_id=run_id)
        operation = lambda: report_module.prepare_run_artifacts(report, paths)
    else:
        operation = lambda: report_module.write_reports(report, output_dir)

    with pytest.raises(ValueError, match=r"duplicate round_id.*7"):
        operation()

    temp_run = output_dir / "runs" / f".{run_id}.tmp"
    assert temp_run.is_dir()
    assert list(temp_run.iterdir()) == []
    assert not (output_dir / "runs" / run_id).exists()


@pytest.mark.asyncio
async def test_all_runtime_backend_artifacts_use_reserved_temp_until_publication(tmp_path: Path):
    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)
    temp_run = _temp_run_dir(request)

    await execute_pipeline(request, evaluator=backend, optimizer=backend)

    artifact_dirs = [call[4] for call in backend.calls if call[0] == "evaluate"]
    artifact_dirs.extend(call[2] for call in backend.calls if call[0] == "optimize")
    assert artifact_dirs
    assert all(path.is_relative_to(temp_run) for path in artifact_dirs)
    assert not temp_run.exists()
    assert _final_run_dir(request).is_dir()


@pytest.mark.asyncio
async def test_backend_crash_leaves_temp_evidence_without_final_publication(tmp_path: Path):
    request = _request(tmp_path)
    backend = _crashing_backend()

    with pytest.raises(RuntimeError, match="simulated backend crash"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    temp_run = _temp_run_dir(request)
    assert (temp_run / "evaluations" / "000_baseline" / "train" / "backend-started.txt").is_file()
    assert not _final_run_dir(request).exists()
    assert not (Path(request.output_dir) / "optimization_report.json").exists()


@pytest.mark.asyncio
async def test_final_artifact_write_failure_cannot_publish_a_partial_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)
    original_write = report_module._atomic_write_text

    def fail_final_markdown(path: Path, content: str) -> None:
        path = Path(path)
        if path.name == "optimization_report.md" and path.parent == _temp_run_dir(request):
            raise OSError("simulated final report write failure")
        original_write(path, content)

    monkeypatch.setattr(report_module, "_atomic_write_text", fail_final_markdown)

    with pytest.raises(OSError, match="simulated final report write failure"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert _temp_run_dir(request).is_dir()
    assert (_temp_run_dir(request) / "optimization_report.json").is_file()
    assert not _final_run_dir(request).exists()
    assert not (Path(request.output_dir) / "optimization_report.json").exists()
    assert not (Path(request.output_dir) / "optimization_report.md").exists()


@pytest.mark.asyncio
async def test_before_publish_failure_refreshes_manifest_without_replacing_original_error(
    tmp_path: Path,
):
    source_root = tmp_path / "callback-source"
    source_root.mkdir()
    source_request = _request(source_root)
    source_report = await execute_pipeline(
        source_request,
        evaluator=_safe_backend(candidate_count=1),
        optimizer=_safe_backend(candidate_count=1),
    )
    run_id = "callback_failure"
    journal = {
        **source_report.audit["writeback_journal"],
        "run_id": run_id,
        "state": "not_requested",
    }
    report = replace(
        source_report,
        run={**source_report.run, "run_id": run_id},
        audit={**source_report.audit, "writeback_journal": journal},
    )
    paths = report_module.reserve_run_artifacts(tmp_path / "callback-output", run_id=run_id)
    report_module.prepare_run_artifacts(report, paths)

    class _PromptIntegritySentinel(RuntimeError):
        pass

    def mutate_journal_then_fail() -> None:
        report_module.persist_writeback_journal(
            paths,
            {
                **journal,
                "state": "unknown",
                "error": "source prompt integrity changed before publication",
                "after_hashes": {"system_prompt": "changed"},
            },
        )
        raise _PromptIntegritySentinel("prompt drift sentinel")

    with pytest.raises(_PromptIntegritySentinel, match="prompt drift sentinel") as caught:
        report_module.finalize_run_artifacts(
            report,
            paths,
            before_publish=mutate_journal_then_fail,
        )

    assert type(caught.value) is _PromptIntegritySentinel
    assert paths.temp_run_dir.is_dir()
    assert not paths.final_run_dir.exists()
    manifest = json.loads(
        (paths.temp_run_dir / "artifact_manifest.json").read_text(encoding="utf-8")
    )
    _assert_manifest_records_match_files(paths.temp_run_dir, manifest)
    journal_record = next(
        record for record in manifest["files"] if record["path"] == "writeback_journal.json"
    )
    journal_bytes = (paths.temp_run_dir / "writeback_journal.json").read_bytes()
    assert journal_record["sha256"] == hashlib.sha256(journal_bytes).hexdigest()
    assert journal_record["size_bytes"] == len(journal_bytes)


@pytest.mark.asyncio
async def test_prepare_rejects_linked_top_level_audit_directory_before_outside_write(
    tmp_path: Path,
):
    source_report = await _source_report(tmp_path, "linked-top-source")
    run_id = "linked_top_level"
    report = replace(source_report, run={**source_report.run, "run_id": run_id})
    paths = report_module.reserve_run_artifacts(tmp_path / "linked-top-output", run_id=run_id)
    outside = tmp_path / "linked-top-outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"outside must remain untouched")
    linked_component = paths.temp_run_dir / "case_results"
    _create_directory_link(linked_component, outside)

    try:
        with pytest.raises(OSError, match="unsafe|reparse|directory|artifact"):
            report_module.prepare_run_artifacts(report, paths)
    finally:
        _remove_directory_link(linked_component)

    assert sentinel.read_bytes() == b"outside must remain untouched"
    assert {item.name for item in outside.iterdir()} == {"sentinel.txt"}
    assert paths.temp_run_dir.is_dir()
    assert not paths.final_run_dir.exists()


@pytest.mark.asyncio
async def test_prepare_rejects_linked_candidate_directory_before_outside_write(tmp_path: Path):
    source_report = await _source_report(tmp_path, "linked-candidate-source")
    run_id = "linked_candidate"
    report = replace(source_report, run={**source_report.run, "run_id": run_id})
    paths = report_module.reserve_run_artifacts(
        tmp_path / "linked-candidate-output",
        run_id=run_id,
    )
    outside = tmp_path / "linked-candidate-outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"candidate outside must remain untouched")
    prompt_root = paths.temp_run_dir / "candidate_prompts"
    prompt_root.mkdir()
    candidate_artifact = report.audit["candidate_artifacts"]["candidate_a"]
    linked_candidate = prompt_root / candidate_artifact
    _create_directory_link(linked_candidate, outside)

    try:
        with pytest.raises(OSError, match="unsafe|reparse|directory|artifact"):
            report_module.prepare_run_artifacts(report, paths)
    finally:
        _remove_directory_link(linked_candidate)

    assert sentinel.read_bytes() == b"candidate outside must remain untouched"
    assert {item.name for item in outside.iterdir()} == {"sentinel.txt"}
    assert paths.temp_run_dir.is_dir()
    assert not paths.final_run_dir.exists()


@pytest.mark.asyncio
async def test_prepare_rejects_non_directory_audit_component_without_replacing_it(tmp_path: Path):
    source_report = await _source_report(tmp_path, "audit-file-source")
    run_id = "audit_component_file"
    report = replace(source_report, run={**source_report.run, "run_id": run_id})
    paths = report_module.reserve_run_artifacts(tmp_path / "audit-file-output", run_id=run_id)
    component = paths.temp_run_dir / "case_results"
    component.write_bytes(b"backend-owned non-directory")

    with pytest.raises(OSError):
        report_module.prepare_run_artifacts(report, paths)

    assert component.read_bytes() == b"backend-owned non-directory"
    assert not paths.final_run_dir.exists()


@pytest.mark.asyncio
async def test_each_nested_audit_write_rechecks_parent_before_next_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source_report = await _source_report(tmp_path, "audit-parent-swap-source")
    run_id = "audit_parent_swap"
    report = replace(source_report, run={**source_report.run, "run_id": run_id})
    paths = report_module.reserve_run_artifacts(tmp_path / "audit-parent-swap-output", run_id=run_id)
    results_dir = paths.temp_run_dir / "case_results"
    parent_became_unsafe = False
    original_write = report_module._atomic_write_text
    original_is_reparse = report_module._is_reparse_point

    def tracked_write(path: Path, content: str) -> None:
        nonlocal parent_became_unsafe
        original_write(path, content)
        if Path(path) == results_dir / "baseline_train.json":
            parent_became_unsafe = True

    def dynamic_reparse(path: Path) -> bool:
        if parent_became_unsafe and Path(path) == results_dir:
            return True
        return original_is_reparse(Path(path))

    monkeypatch.setattr(report_module, "_atomic_write_text", tracked_write)
    monkeypatch.setattr(report_module, "_is_reparse_point", dynamic_reparse)

    with pytest.raises(OSError, match="unsafe|reparse"):
        report_module.prepare_run_artifacts(report, paths)

    assert (results_dir / "baseline_train.json").is_file()
    assert not (results_dir / "baseline_validation.json").exists()
    assert not paths.final_run_dir.exists()


@pytest.mark.asyncio
async def test_nan_serialization_failure_stays_in_temp_and_never_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)
    original_build_report = pipeline_module.build_report

    def report_with_nan(**kwargs: Any):
        report = original_build_report(**kwargs)
        report.audit["non_finite"] = math.nan
        return report

    monkeypatch.setattr(pipeline_module, "build_report", report_with_nan)

    with pytest.raises(ValueError, match="Out of range float values"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert _temp_run_dir(request).is_dir()
    assert not _final_run_dir(request).exists()
    assert not (Path(request.output_dir) / "optimization_report.json").exists()


@pytest.mark.asyncio
async def test_fresh_published_run_has_positive_duration_and_only_strict_json(tmp_path: Path):
    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert report.audit["duration_seconds"] > 0
    for artifact in _final_run_dir(request).rglob("*.json"):
        payload = artifact.read_text(encoding="utf-8")
        json.loads(
            payload,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-standard JSON constant: {value}")
            ),
        )


@pytest.mark.asyncio
async def test_manifest_covers_complete_audit_set_and_partial_candidate_results(tmp_path: Path):
    request = _request(tmp_path)
    delegate = _safe_backend(candidate_count=1)
    backend = FailingCandidateBackend(
        fail_on=("candidate_a", "validation"),
        failure=RuntimeError("validation service unavailable"),
        delegate=delegate,
    )
    backend.rounds = [_round(1, rationale="candidate proposal")]

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    run_dir = _final_run_dir(request)
    manifest = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    _assert_manifest_records_match_files(run_dir, manifest)
    artifacts = manifest["artifacts"]
    assert manifest["schema_version"] == "eval_optimize_loop.artifacts.v1"
    assert manifest["run_id"] == request.run_id
    assert set(artifacts["reports"]) == {"prewrite_json", "prewrite_markdown", "final_json", "final_markdown"}
    assert set(artifacts["baseline_case_results"]) == {"train", "validation"}
    assert set(artifacts["baseline_prompts"]) == {"system_prompt"}
    assert len(artifacts["rounds"]) == 1
    assert set(artifacts["audit_records"]) >= {
        "audit",
        "config_snapshot",
        "input_hashes",
        "rounds",
        "per_case_deltas",
        "gate_decisions",
        "writeback",
        "writeback_journal",
        "evaluation_failures",
    }
    candidate = artifacts["candidates"][0]
    assert candidate["artifact_id"] == report.audit["candidate_artifacts"]["candidate_a"]
    assert set(candidate["prompt_bundle"]) == {"system_prompt"}
    assert set(candidate["case_results"]) == {"train"}
    assert candidate["evaluation_failure"] is True

    declared_files = {item["path"] for item in manifest["files"]}
    for group in (
        artifacts["reports"],
        artifacts["baseline_case_results"],
        artifacts["baseline_prompts"],
        artifacts["audit_records"],
    ):
        assert set(group.values()) <= declared_files
    assert set(artifacts["rounds"]) <= declared_files
    assert json.loads((run_dir / artifacts["rounds"][0]).read_text(encoding="utf-8"))["round_id"] == 1
    assert candidate["diff"] in declared_files
    assert set(candidate["prompt_bundle"].values()) <= declared_files
    assert set(candidate["case_results"].values()) <= declared_files
    assert json.loads((run_dir / artifacts["audit_records"]["evaluation_failures"]).read_text(encoding="utf-8"))[
        "candidate_a"
    ]["stage"] == "validation"


@pytest.mark.asyncio
async def test_convenience_reports_are_exact_copies_created_after_run_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)
    copy_events: list[tuple[str, bool, bool]] = []
    original_write_bytes = report_module._atomic_write_bytes

    def observe_copy(path: Path, content: bytes) -> None:
        path = Path(path)
        if path.parent == Path(request.output_dir) and path.name.startswith("optimization_report"):
            copy_events.append(
                (
                    path.name,
                    _final_run_dir(request).is_dir(),
                    _temp_run_dir(request).exists(),
                )
            )
        original_write_bytes(path, content)

    monkeypatch.setattr(report_module, "_atomic_write_bytes", observe_copy)

    await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert copy_events == [
        ("optimization_report.json", True, False),
        ("optimization_report.md", True, False),
    ]
    for name in ("optimization_report.json", "optimization_report.md"):
        assert (Path(request.output_dir) / name).read_bytes() == (_final_run_dir(request) / name).read_bytes()


@pytest.mark.asyncio
async def test_runtime_input_hashes_match_exact_entry_bytes(tmp_path: Path):
    request = _request(tmp_path)
    exact_inputs = {
        "train": b'{  "eval_cases" : [{"eval_id":"train_case","session_input":{"state":{}}}]}\r\n',
        "validation": b'{"eval_cases":[{"eval_id":"validation_case","session_input":{"state":{}}}]}  \n',
        "optimizer": b'{\r\n  "seed": 91\r\n}\r\n',
    }
    Path(request.train_path).write_bytes(exact_inputs["train"])
    Path(request.validation_path).write_bytes(exact_inputs["validation"])
    Path(request.optimizer_config_path).write_bytes(exact_inputs["optimizer"])
    prompt_bytes = b"baseline with exact trailing spaces  \r\n"
    Path(request.target_prompt_paths["system_prompt"]).write_bytes(prompt_bytes)
    backend = _safe_backend(candidate_count=1)

    await execute_pipeline(request, evaluator=backend, optimizer=backend)

    hashes = json.loads((_final_run_dir(request) / "input_hashes.json").read_text(encoding="utf-8"))
    for role, content in exact_inputs.items():
        assert hashes[role] == hashlib.sha256(content).hexdigest()
    assert hashes["target_prompts"]["system_prompt"] == hashlib.sha256(prompt_bytes).hexdigest()
