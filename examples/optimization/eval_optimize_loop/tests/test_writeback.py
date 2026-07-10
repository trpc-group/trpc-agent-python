from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.eval_loop import writeback
from examples.optimization.eval_optimize_loop.eval_loop.writeback import (
    ConcurrentPromptUpdateError,
)
from examples.optimization.eval_optimize_loop.eval_loop.writeback import (
    commit_prompt_bundle,
)
from examples.optimization.eval_optimize_loop.eval_loop.writeback import (
    snapshot_prompt_files,
)
from examples.optimization.eval_optimize_loop.eval_loop.writeback import (
    temporary_prompt_bundle,
)


def _prompt_files(tmp_path: Path) -> tuple[dict[str, Path], dict[str, bytes]]:
    paths = {
        "system": tmp_path / "system.txt",
        "user": tmp_path / "user.txt",
    }
    baseline = {
        "system": b"system baseline\r\n\xff",
        "user": "user baseline \u57fa\u7ebf\n".encode(),
    }
    for name, path in paths.items():
        path.write_bytes(baseline[name])
    return paths, baseline


def test_snapshot_prompt_files_preserves_bytes_and_returns_hash_copy(tmp_path: Path):
    paths, baseline = _prompt_files(tmp_path)

    snapshot = snapshot_prompt_files(paths)

    system = snapshot.files["system"]
    assert system.name == "system"
    assert system.path == paths["system"]
    assert system.content == baseline["system"]
    assert system.sha256 == hashlib.sha256(baseline["system"]).hexdigest()

    hashes = snapshot.hashes()
    hashes["system"] = "mutated copy"
    assert snapshot.hashes()["system"] == system.sha256


def test_temporary_prompt_bundle_restores_exact_bytes_after_body_error(tmp_path: Path):
    paths, baseline = _prompt_files(tmp_path)
    snapshot = snapshot_prompt_files(paths)

    with pytest.raises(RuntimeError, match="candidate failed"):
        with temporary_prompt_bundle(
            snapshot,
            {"system": "candidate system", "user": "candidate \u7528\u6237"},
        ):
            assert paths["system"].read_bytes() == b"candidate system"
            assert paths["user"].read_bytes() == "candidate \u7528\u6237".encode()
            raise RuntimeError("candidate failed")

    assert {name: path.read_bytes() for name, path in paths.items()} == baseline


def test_temporary_prompt_bundle_rejects_missing_prompt_before_writing(tmp_path: Path):
    paths, baseline = _prompt_files(tmp_path)
    snapshot = snapshot_prompt_files(paths)

    with pytest.raises(ValueError, match="user"):
        with temporary_prompt_bundle(snapshot, {"system": "candidate"}):
            pytest.fail("incomplete prompt bundle must not be entered")

    assert {name: path.read_bytes() for name, path in paths.items()} == baseline


def test_commit_rejects_concurrent_update_before_any_write(tmp_path: Path, monkeypatch):
    paths, baseline = _prompt_files(tmp_path)
    snapshot = snapshot_prompt_files(paths)
    external_content = b"updated by another process"
    paths["user"].write_bytes(external_content)

    def unexpected_write(path: Path, content: bytes) -> None:
        pytest.fail(f"unexpected write to {path} with {content!r}")

    monkeypatch.setattr(writeback, "_atomic_replace_bytes", unexpected_write)

    with pytest.raises(ConcurrentPromptUpdateError, match="changed"):
        commit_prompt_bundle(
            snapshot,
            {"system": "candidate system", "user": "candidate user"},
        )

    assert paths["system"].read_bytes() == baseline["system"]
    assert paths["user"].read_bytes() == external_content


def test_commit_rejects_missing_prompt_before_writing(tmp_path: Path, monkeypatch):
    paths, baseline = _prompt_files(tmp_path)
    snapshot = snapshot_prompt_files(paths)

    def unexpected_write(path: Path, content: bytes) -> None:
        pytest.fail(f"unexpected write to {path} with {content!r}")

    monkeypatch.setattr(writeback, "_atomic_replace_bytes", unexpected_write)

    with pytest.raises(ValueError, match="user"):
        commit_prompt_bundle(snapshot, {"system": "candidate"})

    assert {name: path.read_bytes() for name, path in paths.items()} == baseline


def test_commit_applies_complete_bundle_and_reports_hashes(tmp_path: Path):
    paths, _ = _prompt_files(tmp_path)
    snapshot = snapshot_prompt_files(paths)
    prompts = {"system": "new system", "user": "new \u7528\u6237"}

    result = commit_prompt_bundle(snapshot, prompts)

    assert result.status == "applied"
    assert result.before_hashes == snapshot.hashes()
    assert result.after_hashes == {name: hashlib.sha256(prompts[name].encode()).hexdigest() for name in paths}
    assert result.error is None
    assert {name: path.read_bytes() for name, path in paths.items()} == {name: prompts[name].encode() for name in paths}


def test_commit_rolls_back_when_post_write_hashing_fails(tmp_path: Path, monkeypatch):
    paths, baseline = _prompt_files(tmp_path)
    snapshot = snapshot_prompt_files(paths)
    original_read_bytes = Path.read_bytes
    hash_read_failed = False

    def fail_once_for_candidate_system(path: Path):
        nonlocal hash_read_failed
        content = original_read_bytes(path)
        if not hash_read_failed and path == paths["system"] and content == b"candidate system":
            hash_read_failed = True
            raise OSError("post-write hash read failed")
        return content

    monkeypatch.setattr(Path, "read_bytes", fail_once_for_candidate_system)

    result = commit_prompt_bundle(
        snapshot,
        {"system": "candidate system", "user": "candidate user"},
    )

    assert hash_read_failed
    assert result.status == "rolled_back"
    assert result.before_hashes == snapshot.hashes()
    assert result.after_hashes == snapshot.hashes()
    assert result.error is not None
    assert "post-write hash read failed" in result.error
    assert {name: path.read_bytes() for name, path in paths.items()} == baseline


def test_commit_removes_temp_file_when_replace_fails(tmp_path: Path, monkeypatch):
    target = tmp_path / "system.txt"
    baseline = b"system baseline"
    target.write_bytes(baseline)
    snapshot = snapshot_prompt_files({"system": target})
    original_replace = os.replace
    replace_calls = 0

    def fail_first_replace(source: str | bytes | Path, destination: str | bytes | Path):
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 1:
            raise OSError("candidate replace failed")
        return original_replace(source, destination)

    monkeypatch.setattr(writeback.os, "replace", fail_first_replace)

    result = commit_prompt_bundle(snapshot, {"system": "candidate system"})

    assert result.status == "rolled_back"
    assert target.read_bytes() == baseline
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_atomic_replace_preserves_replace_error_when_temp_unlink_fails(tmp_path: Path, monkeypatch):
    target = tmp_path / "system.txt"
    baseline = b"system baseline"
    target.write_bytes(baseline)
    original_unlink = Path.unlink

    def fail_replace(source: str | bytes | Path, destination: str | bytes | Path):
        raise OSError("primary replace failed")

    def fail_temp_unlink(path: Path, missing_ok: bool = False):
        if path.parent == tmp_path and path.name.startswith(f".{target.name}.") and path.suffix == ".tmp":
            raise OSError("temp unlink failed")
        return original_unlink(path, missing_ok=missing_ok)

    with monkeypatch.context() as injected_failure:
        injected_failure.setattr(writeback.os, "replace", fail_replace)
        injected_failure.setattr(Path, "unlink", fail_temp_unlink)
        with pytest.raises(OSError) as error_info:
            writeback._atomic_replace_bytes(target, b"candidate system")

    for temp_path in tmp_path.glob(f".{target.name}.*.tmp"):
        original_unlink(temp_path)

    assert str(error_info.value) == "primary replace failed"
    assert target.read_bytes() == baseline


def test_commit_rolls_back_all_files_when_second_replace_fails(tmp_path: Path, monkeypatch):
    paths, baseline = _prompt_files(tmp_path)
    snapshot = snapshot_prompt_files(paths)
    original_replace = os.replace
    replace_calls = 0

    def fail_second_replace(source: str | bytes | Path, destination: str | bytes | Path):
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("second replace failed")
        return original_replace(source, destination)

    monkeypatch.setattr(writeback.os, "replace", fail_second_replace)

    result = commit_prompt_bundle(
        snapshot,
        {"system": "candidate system", "user": "candidate user"},
    )

    assert result.status == "rolled_back"
    assert result.before_hashes == snapshot.hashes()
    assert result.after_hashes == snapshot.hashes()
    assert result.error is not None
    assert "second replace failed" in result.error
    assert replace_calls == 4
    assert {name: path.read_bytes() for name, path in paths.items()} == baseline


def test_commit_reports_rollback_failed_when_restored_hashes_differ(tmp_path: Path, monkeypatch):
    paths, _ = _prompt_files(tmp_path)
    snapshot = snapshot_prompt_files(paths)
    original_replace = os.replace
    original_restore = writeback._restore_snapshot
    replace_calls = 0

    def fail_second_replace(source: str | bytes | Path, destination: str | bytes | Path):
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("candidate write failed")
        return original_replace(source, destination)

    def restore_then_concurrently_update(snapshot_to_restore):
        failures = original_restore(snapshot_to_restore)
        paths["system"].write_bytes(b"concurrent update after restore")
        return failures

    monkeypatch.setattr(writeback.os, "replace", fail_second_replace)
    monkeypatch.setattr(writeback, "_restore_snapshot", restore_then_concurrently_update)

    result = commit_prompt_bundle(
        snapshot,
        {"system": "candidate system", "user": "candidate user"},
    )

    assert result.status == "rollback_failed"
    assert result.after_hashes["system"] != snapshot.hashes()["system"]
    assert result.error is not None
    assert "system: restored hash differs from snapshot" in result.error


def test_commit_reports_rollback_failed_and_attempts_every_restore(tmp_path: Path, monkeypatch):
    paths, baseline = _prompt_files(tmp_path)
    snapshot = snapshot_prompt_files(paths)
    original_replace = os.replace
    replace_calls = 0

    def fail_write_and_first_restore(
        source: str | bytes | Path,
        destination: str | bytes | Path,
    ):
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("candidate write failed")
        if replace_calls == 3:
            raise OSError("system restore failed")
        return original_replace(source, destination)

    monkeypatch.setattr(writeback.os, "replace", fail_write_and_first_restore)

    result = commit_prompt_bundle(
        snapshot,
        {"system": "candidate system", "user": "candidate user"},
    )

    assert result.status == "rollback_failed"
    assert result.before_hashes == snapshot.hashes()
    assert result.after_hashes["system"] != snapshot.hashes()["system"]
    assert result.after_hashes["user"] == snapshot.hashes()["user"]
    assert result.error is not None
    assert "candidate write failed" in result.error
    assert "system: system restore failed" in result.error
    assert replace_calls == 4
    assert paths["system"].read_bytes() == b"candidate system"
    assert paths["user"].read_bytes() == baseline["user"]
