"""Prompt lifecycle, audit persistence, sanitization and cost tests."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from trpc_agent_sdk.evaluation import TargetPrompt

from examples.optimization.eval_optimize_loop.pipeline.artifacts import AuditSink
from examples.optimization.eval_optimize_loop.pipeline import artifacts as artifacts_module
from examples.optimization.eval_optimize_loop.pipeline.costing import CostLedger
from examples.optimization.eval_optimize_loop.pipeline.prompt_workspace import (
    PromptRestoreError,
    PromptRunLock,
    PromptWorkspace,
)
from examples.optimization.eval_optimize_loop.pipeline.schema import sanitized_text


def _directory_link_or_emulation(link: Path, target: Path, monkeypatch) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        link.mkdir()
        original = getattr(Path, "is_junction", None)
        monkeypatch.setattr(
            Path,
            "is_junction",
            lambda path: path == link or (bool(original(path)) if original else False),
            raising=False,
        )


def _contend_for_prompt_lock(prompt_path: str, lock_root: str, start, release, outcomes) -> None:
    lock = PromptRunLock((prompt_path, ), lock_root=lock_root)
    start.wait(timeout=10)
    try:
        lock.acquire()
    except RuntimeError:
        outcomes.put("blocked")
        return
    try:
        outcomes.put("acquired")
        release.wait(timeout=10)
    finally:
        lock.release()


@pytest.mark.asyncio
async def test_temporary_candidate_always_restores_baseline(tmp_path) -> None:
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("baseline", encoding="utf-8")
    workspace = PromptWorkspace(TargetPrompt().add_path("system", str(prompt_path)))
    await workspace.initialize()
    with pytest.raises(RuntimeError, match="candidate failed"):
        async with workspace.temporary({"system": "candidate"}):
            assert prompt_path.read_text(encoding="utf-8") == "candidate"
            raise RuntimeError("candidate failed")
    assert prompt_path.read_text(encoding="utf-8") == "baseline"
    assert await workspace.current_hashes() == workspace.baseline_hashes


@pytest.mark.asyncio
async def test_apply_is_verified_and_can_be_rolled_back(tmp_path) -> None:
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("baseline", encoding="utf-8")
    workspace = PromptWorkspace(TargetPrompt().add_path("system", str(prompt_path)))
    await workspace.initialize()
    hashes = await workspace.apply({"system": "candidate"})
    assert hashes["system"] == hashlib.sha256(b"candidate").hexdigest()
    await workspace.restore()
    assert prompt_path.read_text(encoding="utf-8") == "baseline"


def test_prompt_run_lock_rejects_concurrent_owner_and_releases(tmp_path) -> None:
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("baseline", encoding="utf-8")
    lock_root = str(tmp_path / "locks")
    first = PromptRunLock((str(prompt_path), ), lock_root=lock_root)
    second = PromptRunLock((str(prompt_path), ), lock_root=lock_root)
    with first:
        with pytest.raises(RuntimeError, match="already owned"):
            second.acquire()
    with second:
        assert second.path == first.path


def test_prompt_run_lock_first_acquire_has_one_sentinel_byte(tmp_path) -> None:
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("baseline", encoding="utf-8")
    lock_root = str(tmp_path / "locks")
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    release = context.Event()
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_contend_for_prompt_lock,
            args=(str(prompt_path), lock_root, start, release, outcomes),
        ) for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    try:
        results = [outcomes.get(timeout=15) for _ in processes]
    finally:
        release.set()
        for process in processes:
            process.join(timeout=15)

    assert sorted(results) == ["acquired", "blocked"]
    assert all(process.exitcode == 0 for process in processes)
    lock_path = PromptRunLock((str(prompt_path), ), lock_root=lock_root).path
    assert lock_path.read_bytes() == b"\0"


@pytest.mark.asyncio
async def test_restoration_failure_preserves_the_primary_operation_diagnostic() -> None:
    state = {"value": "baseline", "fail_restore": False}

    async def read() -> str:
        return state["value"]

    async def write(value: str) -> None:
        if value == "baseline" and state["fail_restore"]:
            raise OSError("restore unavailable")
        state["value"] = value

    workspace = PromptWorkspace(TargetPrompt().add_callback("system", read=read, write=write))
    await workspace.initialize()
    with pytest.raises(PromptRestoreError) as raised:
        async with workspace.temporary({"system": "candidate"}):
            state["fail_restore"] = True
            raise RuntimeError("candidate evaluation failed")
    message = sanitized_text(raised.value, max_text_chars=4000)
    assert "candidate evaluation failed" in message
    assert "restore unavailable" in message


@pytest.mark.asyncio
async def test_apply_double_failure_preserves_write_and_restore_diagnostics() -> None:
    async def read() -> str:
        return "baseline"

    async def write(value: str) -> None:
        if value == "candidate":
            raise OSError("candidate write unavailable")
        raise OSError("restore unavailable")

    workspace = PromptWorkspace(TargetPrompt().add_callback("system", read=read, write=write))
    await workspace.initialize()
    with pytest.raises(PromptRestoreError) as raised:
        await workspace.apply({"system": "candidate"})
    message = sanitized_text(raised.value, max_text_chars=4000)
    assert "candidate write unavailable" in message
    assert "restore unavailable" in message


def test_audit_sink_is_immutable_safe_sanitized_and_manifested(tmp_path) -> None:
    sink = AuditSink(tmp_path / "artifacts", "run-1")
    sink.create()
    sink.write_json(
        "config.json",
        {
            "api_key": "secret-value",
            "nested": {
                "message": "Authorization: Bearer abc.def"
            },
            "tokenUsage": {
                "total": 12
            },
        },
    )
    sink.write_text("notes.txt", "token=abc123")
    with pytest.raises(ValueError, match="unsafe|path"):
        sink.write_text("../escape.txt", "bad")
    records = sink.write_manifest()
    payload = json.loads((sink.run_dir / "config.json").read_text(encoding="utf-8"))
    assert payload["api_key"] == "[REDACTED]"
    assert "abc.def" not in payload["nested"]["message"]
    assert payload["tokenUsage"] == {"total": 12}
    assert {record.path for record in records} == {"config.json", "notes.txt"}
    for record in records:
        content = (sink.run_dir / record.path).read_bytes()
        assert record.sha256 == hashlib.sha256(content).hexdigest()
        assert record.byte_size == len(content)
    with pytest.raises(FileExistsError):
        AuditSink(tmp_path / "artifacts", "run-1").create()


def test_audit_sink_preserves_complete_text_and_fails_explicitly_on_size_limit(tmp_path) -> None:
    content = "complete-audit-content:" + "x" * 256
    sink = AuditSink(tmp_path / "artifacts", "complete", max_file_bytes=1024)
    sink.create()
    sink.write_text("prompt.md", content)
    sink.write_json("prompt.json", {"prompt": content})
    assert (sink.run_dir / "prompt.md").read_text(encoding="utf-8") == content
    assert json.loads((sink.run_dir / "prompt.json").read_text(encoding="utf-8"))["prompt"] == content

    limited = AuditSink(tmp_path / "artifacts", "limited", max_file_bytes=64)
    limited.create()
    with pytest.raises(ValueError, match="audit file exceeds byte limit"):
        limited.write_text("prompt.md", content)
    assert not (limited.run_dir / "prompt.md").exists()


def test_optimizer_tree_is_sanitized_before_audit_import(tmp_path) -> None:
    raw = tmp_path / "raw-optimizer"
    raw.mkdir()
    (raw / "config.snapshot.json").write_text(
        json.dumps({"optimize": {
            "apiKey": "live-secret"
        }}),
        encoding="utf-8",
    )
    (raw / "run.log").write_text(
        'Authorization: Bearer live-token\n{"apiKey":"quoted-log-secret"}',
        encoding="utf-8",
    )
    (raw / "summary.md").write_text(
        "result: {'clientSecret': 'markdown-secret'}",
        encoding="utf-8",
    )
    sink = AuditSink(tmp_path / "artifacts", "run-2")
    sink.create()
    sink.import_tree(raw, "candidate_generation/optimizer")
    imported = "\n".join(path.read_text(encoding="utf-8") for path in sink.run_dir.rglob("*") if path.is_file())
    assert "live-secret" not in imported
    assert "live-token" not in imported
    assert "quoted-log-secret" not in imported
    assert "markdown-secret" not in imported
    assert "[REDACTED]" in imported


def test_optimizer_tree_rejects_source_directory_symlink(tmp_path, monkeypatch) -> None:
    raw = tmp_path / "raw-optimizer"
    raw.mkdir()
    (raw / "secret.log").write_text("secret", encoding="utf-8")
    alias = tmp_path / "optimizer-alias"
    _directory_link_or_emulation(alias, raw, monkeypatch)

    sink = AuditSink(tmp_path / "artifacts", "run-root-link")
    sink.create()
    with pytest.raises(ValueError, match="symlinks"):
        sink.import_tree(alias, "candidate_generation/optimizer")


def test_optimizer_tree_rejects_nested_directory_symlink(tmp_path, monkeypatch) -> None:
    raw = tmp_path / "raw-optimizer"
    external = tmp_path / "external"
    raw.mkdir()
    external.mkdir()
    (external / "secret.log").write_text("secret", encoding="utf-8")
    _directory_link_or_emulation(raw / "linked", external, monkeypatch)

    sink = AuditSink(tmp_path / "artifacts", "run-nested-link")
    sink.create()
    with pytest.raises(ValueError, match="symlinks"):
        sink.import_tree(raw, "candidate_generation/optimizer")


def test_optimizer_tree_rejects_nested_directory_junction(tmp_path, monkeypatch) -> None:
    raw = tmp_path / "raw-optimizer"
    linked = raw / "linked"
    linked.mkdir(parents=True)
    (linked / "outside.log").write_text("outside", encoding="utf-8")
    original = getattr(Path, "is_junction", None)

    def is_junction(path: Path) -> bool:
        return path == linked or (bool(original(path)) if original else False)

    monkeypatch.setattr(Path, "is_junction", is_junction, raising=False)
    sink = AuditSink(tmp_path / "artifacts", "run-nested-junction")
    sink.create()

    with pytest.raises(ValueError, match="reparse points"):
        sink.import_tree(raw, "candidate_generation/optimizer")


def test_optimizer_tree_rejects_broken_source_junction(tmp_path, monkeypatch) -> None:
    junction = tmp_path / "broken-junction"
    original = getattr(Path, "is_junction", None)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda path: path == junction or (bool(original(path)) if original else False),
        raising=False,
    )
    sink = AuditSink(tmp_path / "artifacts", "run-broken-junction")
    sink.create()

    with pytest.raises(ValueError, match="reparse points"):
        sink.import_tree(junction, "candidate_generation/optimizer")


def test_optimizer_tree_rejects_hard_link_to_external_file(tmp_path) -> None:
    raw = tmp_path / "raw-optimizer"
    raw.mkdir()
    external = tmp_path / "external.log"
    external.write_text("outside-content", encoding="utf-8")
    os.link(external, raw / "outside.log")

    sink = AuditSink(tmp_path / "artifacts", "run-hard-link")
    sink.create()
    with pytest.raises(ValueError, match="hard links"):
        sink.import_tree(raw, "candidate_generation/optimizer")


def test_optimizer_tree_validates_all_files_before_publishing(tmp_path) -> None:
    raw = tmp_path / "raw-optimizer"
    raw.mkdir()
    (raw / "valid.log").write_text("valid", encoding="utf-8")
    (raw / "invalid.bin").write_bytes(b"invalid")

    sink = AuditSink(tmp_path / "artifacts", "run-batch-validation")
    sink.create()
    with pytest.raises(ValueError, match="unsupported"):
        sink.import_tree(raw, "candidate_generation/optimizer")
    assert not (sink.run_dir / "candidate_generation").exists()


def test_optimizer_tree_rejects_file_identity_change_before_read(tmp_path, monkeypatch) -> None:
    raw = tmp_path / "raw-optimizer"
    raw.mkdir()
    changed = raw / "changed.log"
    changed.write_text("before", encoding="utf-8")
    original_open = artifacts_module.os.open

    def replace_before_open(path, flags, *args):
        if Path(path) == changed:
            changed.write_text("after", encoding="utf-8")
        return original_open(path, flags, *args)

    monkeypatch.setattr(artifacts_module.os, "open", replace_before_open)
    sink = AuditSink(tmp_path / "artifacts", "run-identity-change")
    sink.create()
    with pytest.raises(ValueError, match="changed during validation"):
        sink.import_tree(raw, "candidate_generation/optimizer")


@pytest.mark.parametrize(
    ("sink_kwargs", "files", "message"),
    [
        ({
            "max_import_files": 1
        }, {
            "a.log": "a",
            "b.log": "b"
        }, "count"),
        ({
            "max_import_file_bytes": 3
        }, {
            "large.log": "1234"
        }, "per-file"),
        (
            {
                "max_import_file_bytes": 4,
                "max_import_total_bytes": 5
            },
            {
                "a.log": "123",
                "b.log": "456"
            },
            "total",
        ),
    ],
)
def test_optimizer_tree_enforces_resource_budgets(tmp_path, sink_kwargs, files, message) -> None:
    raw = tmp_path / "raw-budget"
    raw.mkdir()
    for name, content in files.items():
        (raw / name).write_text(content, encoding="utf-8")
    sink = AuditSink(
        tmp_path / "artifacts",
        "run-budget",
        **sink_kwargs,
    )
    sink.create()
    with pytest.raises(ValueError, match=message):
        sink.import_tree(raw, "candidate_generation/optimizer")


def test_concurrent_latest_publication_uses_collision_free_atomic_files(tmp_path) -> None:
    publication_root = tmp_path / "publication"
    sinks = []
    for index in range(2):
        sink = AuditSink(
            tmp_path / "artifacts",
            f"run-{index}",
            publication_root=publication_root,
        )
        sink.create()
        sink.write_text("report.md", f"run-{index}\n")
        sinks.append(sink)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(
                sinks[index % 2].publish_latest_snapshot,
                "report.md",
                "optimization_report.md",
            ) for index in range(40)
        ]
        for future in futures:
            future.result()

    assert (publication_root / "optimization_report.md").read_text(encoding="utf-8") in {
        "run-0\n",
        "run-1\n",
    }
    assert not list(publication_root.glob("*.tmp"))


def test_cost_ledger_preserves_unknown_sources() -> None:
    ledger = CostLedger()
    ledger.record("fake", cost_usd=0, metric_calls=3)
    ledger.record("live-judge", cost_usd=None, model_calls=1)
    summary = ledger.summary()
    assert summary.total_cost_usd is None
    assert [source.name for source in summary.sources] == ["fake", "live-judge"]


def test_cost_ledger_preserves_upper_bound_basis() -> None:
    ledger = CostLedger()
    ledger.record("live-evaluation", cost_usd=0.5, upper_bound=True, model_calls=2)
    summary = ledger.summary()
    assert summary.total_cost_usd == 0.5
    assert summary.sources[0].upper_bound is True
