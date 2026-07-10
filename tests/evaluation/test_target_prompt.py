# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for TargetPrompt."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable
from unittest import mock

import pytest

from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_add_path_returns_self_for_chaining(tmp_path: Path):
    p1 = _write(tmp_path / "a.md", "A")
    p2 = _write(tmp_path / "b.md", "B")
    target = TargetPrompt().add_path("a", str(p1)).add_path("b", str(p2))
    assert target.names() == ["a", "b"]


def test_add_callback_returns_self_for_chaining():
    async def _read() -> str:
        return "x"

    async def _write_fn(value: str) -> None:
        return None

    target = (
        TargetPrompt()
        .add_callback("c1", read=_read, write=_write_fn)
        .add_callback("c2", read=_read, write=_write_fn)
    )
    assert target.names() == ["c1", "c2"]


def test_names_in_registration_order(tmp_path: Path):
    p = _write(tmp_path / "x.md", "x")

    async def _read() -> str:
        return ""

    async def _write_fn(value: str) -> None:
        return None

    target = (
        TargetPrompt()
        .add_path("first", str(p))
        .add_callback("second", read=_read, write=_write_fn)
        .add_path("third", str(p))
    )
    assert target.names() == ["first", "second", "third"]


def test_add_path_duplicate_name_raises_value_error(tmp_path: Path):
    p = _write(tmp_path / "a.md", "A")
    target = TargetPrompt().add_path("a", str(p))
    with pytest.raises(ValueError, match="already registered"):
        target.add_path("a", str(p))


def test_add_callback_duplicate_name_raises_value_error():
    async def _read() -> str:
        return ""

    async def _write_fn(value: str) -> None:
        return None

    target = TargetPrompt().add_callback("c", read=_read, write=_write_fn)
    with pytest.raises(ValueError, match="already registered"):
        target.add_callback("c", read=_read, write=_write_fn)


def test_add_path_and_callback_same_name_raises(tmp_path: Path):
    p = _write(tmp_path / "a.md", "A")

    async def _read() -> str:
        return ""

    async def _write_fn(value: str) -> None:
        return None

    target = TargetPrompt().add_path("a", str(p))
    with pytest.raises(ValueError, match="already registered"):
        target.add_callback("a", read=_read, write=_write_fn)


def test_empty_target_prompt_names_is_empty():
    assert TargetPrompt().names() == []


def test_add_callback_requires_async_read_callable():
    def _sync_read() -> str:
        return ""

    async def _write_fn(value: str) -> None:
        return None

    with pytest.raises(TypeError, match="async"):
        TargetPrompt().add_callback("c", read=_sync_read, write=_write_fn)


def test_add_callback_requires_async_write_callable():
    async def _read() -> str:
        return ""

    def _sync_write(value: str) -> None:
        return None

    with pytest.raises(TypeError, match="async"):
        TargetPrompt().add_callback("c", read=_read, write=_sync_write)


@pytest.mark.asyncio
async def test_read_all_with_paths(tmp_path: Path):
    p1 = _write(tmp_path / "a.md", "alpha")
    p2 = _write(tmp_path / "b.md", "beta")
    target = TargetPrompt().add_path("a", str(p1)).add_path("b", str(p2))
    assert await target.read_all() == {"a": "alpha", "b": "beta"}


@pytest.mark.parametrize(
    "original_bytes",
    [
        "system prompt\nkeep exact bytes\n".encode("utf-8"),
        "system prompt\r\nkeep exact bytes\r\n".encode("utf-8"),
        "lf\ncrlf\r\nlone carriage return\rend".encode("utf-8"),
    ],
    ids=["lf", "crlf", "mixed"],
)
@pytest.mark.asyncio
async def test_path_read_write_round_trip_preserves_newline_bytes(
    tmp_path: Path,
    original_bytes: bytes,
):
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_bytes(original_bytes)
    target = TargetPrompt().add_path("prompt", str(prompt_path))

    snapshot = await target.read_all()
    await target.write_all(snapshot)

    assert prompt_path.read_bytes() == original_bytes


@pytest.mark.asyncio
async def test_read_all_path_not_exist_raises_file_not_found(tmp_path: Path):
    target = TargetPrompt().add_path("missing", str(tmp_path / "ghost.md"))
    with pytest.raises(FileNotFoundError):
        await target.read_all()


@pytest.mark.asyncio
async def test_read_all_with_async_callback():
    async def _read() -> str:
        return "callback-value"

    async def _write_fn(value: str) -> None:
        return None

    target = TargetPrompt().add_callback("k", read=_read, write=_write_fn)
    assert await target.read_all() == {"k": "callback-value"}


@pytest.mark.asyncio
async def test_read_all_callback_raises_propagates():
    async def _read() -> str:
        raise RuntimeError("remote down")

    async def _write_fn(value: str) -> None:
        return None

    target = TargetPrompt().add_callback("k", read=_read, write=_write_fn)
    with pytest.raises(RuntimeError, match="remote down"):
        await target.read_all()


@pytest.mark.asyncio
async def test_read_all_mixed_path_and_callback(tmp_path: Path):
    p = _write(tmp_path / "p.md", "from-file")

    async def _read() -> str:
        return "from-callback"

    async def _write_fn(value: str) -> None:
        return None

    target = (
        TargetPrompt()
        .add_path("a", str(p))
        .add_callback("b", read=_read, write=_write_fn)
    )
    assert await target.read_all() == {"a": "from-file", "b": "from-callback"}


@pytest.mark.asyncio
async def test_read_single_field(tmp_path: Path):
    p = _write(tmp_path / "a.md", "single")
    target = TargetPrompt().add_path("a", str(p))
    assert await target.read("a") == "single"


@pytest.mark.asyncio
async def test_read_unknown_name_raises_key_error():
    target = TargetPrompt()
    with pytest.raises(KeyError):
        await target.read("nope")


@pytest.mark.asyncio
async def test_write_all_with_paths_updates_files(tmp_path: Path):
    p1 = _write(tmp_path / "a.md", "old-a")
    p2 = _write(tmp_path / "b.md", "old-b")
    target = TargetPrompt().add_path("a", str(p1)).add_path("b", str(p2))
    await target.write_all({"a": "new-a", "b": "new-b"})
    assert p1.read_text(encoding="utf-8") == "new-a"
    assert p2.read_text(encoding="utf-8") == "new-b"


@pytest.mark.asyncio
async def test_write_all_with_callback_invokes_write():
    received: dict[str, str] = {}

    async def _read() -> str:
        return received.get("k", "")

    async def _write_fn(value: str) -> None:
        received["k"] = value

    target = TargetPrompt().add_callback("k", read=_read, write=_write_fn)
    await target.write_all({"k": "callback-payload"})
    assert received == {"k": "callback-payload"}


@pytest.mark.asyncio
async def test_write_all_keys_mismatch_raises(tmp_path: Path):
    p = _write(tmp_path / "a.md", "A")
    target = TargetPrompt().add_path("a", str(p))

    with pytest.raises(ValueError, match="mismatch"):
        await target.write_all({})

    with pytest.raises(ValueError, match="mismatch"):
        await target.write_all({"a": "ok", "extra": "x"})


@pytest.mark.asyncio
async def test_write_all_no_tmp_file_remains_on_success(tmp_path: Path):
    p = _write(tmp_path / "a.md", "old")
    target = TargetPrompt().add_path("a", str(p))
    await target.write_all({"a": "new"})
    assert p.read_text(encoding="utf-8") == "new"
    leftover = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftover == []


@pytest.mark.asyncio
async def test_write_all_atomic_rollback_on_partial_failure(tmp_path: Path):
    p1 = _write(tmp_path / "a.md", "old-a")
    p2 = _write(tmp_path / "b.md", "old-b")
    target = TargetPrompt().add_path("a", str(p1)).add_path("b", str(p2))

    original_replace = os.replace
    seen: dict[str, int] = {"count": 0}

    def _flaky_replace(src: str, dst: str) -> None:
        seen["count"] += 1
        if seen["count"] == 2:
            raise OSError("simulated disk failure on second rename")
        return original_replace(src, dst)

    with mock.patch("os.replace", side_effect=_flaky_replace):
        with pytest.raises(OSError, match="simulated"):
            await target.write_all({"a": "new-a", "b": "new-b"})

    # Atomicity contract: every source file is restored to its pre-call content.
    assert p1.read_text(encoding="utf-8") == "old-a"
    assert p2.read_text(encoding="utf-8") == "old-b"
    leftover = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftover == []


# ---------------------------------------------------------------------------
# CONC-3 fix: rollback uses atomic primitives + best-effort failure aggregation.
# Test matrix: T1/T2/T7 already covered above; below adds T3-T8 + edge cases.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_all_callback_failure_rolls_back_paths(tmp_path: Path):
    """T4: callback write fails after path writes succeed; path fields must
    be restored to baseline. The callback failure is propagated."""
    p1 = _write(tmp_path / "a.md", "old-a")
    p2 = _write(tmp_path / "b.md", "old-b")

    async def _read() -> str:
        return ""

    async def _write_fn(value: str) -> None:
        raise RuntimeError("simulated KV write failure")

    target = (
        TargetPrompt()
        .add_path("a", str(p1))
        .add_path("b", str(p2))
        .add_callback("c", read=_read, write=_write_fn)
    )

    with pytest.raises(RuntimeError, match="simulated KV"):
        await target.write_all({"a": "new-a", "b": "new-b", "c": "new-c"})

    assert p1.read_text(encoding="utf-8") == "old-a"
    assert p2.read_text(encoding="utf-8") == "old-b"
    leftover = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftover == []


@pytest.mark.asyncio
async def test_write_all_rolls_back_to_unlink_when_baseline_absent(tmp_path: Path):
    """T5: file did not exist before write_all (backup=None); rollback path
    must unlink the file rather than restore content."""
    p1 = _write(tmp_path / "a.md", "old-a")
    ghost = tmp_path / "ghost.md"
    assert not ghost.exists()

    target = TargetPrompt().add_path("ghost", str(ghost)).add_path("a", str(p1))

    original_replace = os.replace
    seen = {"count": 0}

    def _flaky_replace(src: str, dst: str) -> None:
        seen["count"] += 1
        # registration order: ghost first, a second
        # call 1 = ghost.md.tmp -> ghost.md (succeeds, ghost newly created)
        # call 2 = a.md.tmp -> a.md (fails -> rollback for [ghost])
        if seen["count"] == 2:
            raise OSError("simulated failure on second rename")
        return original_replace(src, dst)

    with mock.patch("os.replace", side_effect=_flaky_replace):
        with pytest.raises(OSError, match="simulated"):
            await target.write_all({"ghost": "new-ghost", "a": "new-a"})

    assert not ghost.exists()
    assert p1.read_text(encoding="utf-8") == "old-a"
    leftover = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftover == []


@pytest.mark.asyncio
async def test_write_all_rollback_failure_aggregates_and_chains_root_cause(tmp_path: Path):
    """T3+T6: both forward write and rollback restore fail. Aggregate
    _RollbackError lists the failed field; root cause preserved on __cause__."""
    from trpc_agent_sdk.evaluation._target_prompt import _RollbackError

    p1 = _write(tmp_path / "a.md", "baseline-a")
    p2 = _write(tmp_path / "b.md", "baseline-b")
    target = TargetPrompt().add_path("a", str(p1)).add_path("b", str(p2))

    original_replace = os.replace
    call_count = {"n": 0}

    def _flaky_replace(src: str, dst: str) -> None:
        call_count["n"] += 1
        # call 1: forward a.md (succeeds)
        # call 2: forward b.md (fails -> rollback for [a])
        # call 3: rollback a-restore (fails too)
        if call_count["n"] == 2:
            raise OSError("primary write failure")
        if call_count["n"] >= 3:
            raise PermissionError("rollback restore denied")
        return original_replace(src, dst)

    with mock.patch("os.replace", side_effect=_flaky_replace):
        with pytest.raises(_RollbackError) as excinfo:
            await target.write_all({"a": "new-a", "b": "new-b"})

    err = excinfo.value
    assert "a" in str(err)
    assert "PermissionError" in str(err)
    assert isinstance(err.__cause__, OSError)
    assert "primary write failure" in str(err.__cause__)
    assert len(err.failures) == 1
    assert err.failures[0][0] == "a"
    assert isinstance(err.failures[0][1], PermissionError)


@pytest.mark.asyncio
async def test_write_all_rollback_unlink_failure_aggregated(tmp_path: Path):
    """T6 variant: backup=None case; unlink fails -> _RollbackError carries it."""
    from trpc_agent_sdk.evaluation._target_prompt import _RollbackError

    p1 = _write(tmp_path / "a.md", "baseline-a")
    ghost = tmp_path / "ghost.md"
    target = TargetPrompt().add_path("ghost", str(ghost)).add_path("a", str(p1))

    original_replace = os.replace
    original_unlink = os.unlink
    state = {"replace_count": 0}

    def _flaky_replace(src: str, dst: str) -> None:
        state["replace_count"] += 1
        # call 1: ghost.md.tmp -> ghost.md (succeeds)
        # call 2: a.md.tmp -> a.md (fails -> rollback for [ghost])
        if state["replace_count"] == 2:
            raise OSError("primary failure on a.md")
        return original_replace(src, dst)

    def _flaky_unlink(path: str) -> None:
        if str(path) == str(ghost):
            raise PermissionError("unlink denied")
        return original_unlink(path)

    with mock.patch("os.replace", side_effect=_flaky_replace), \
         mock.patch("os.unlink", side_effect=_flaky_unlink):
        with pytest.raises(_RollbackError) as excinfo:
            await target.write_all({"ghost": "g", "a": "new-a"})

    err = excinfo.value
    assert "ghost" in str(err)
    assert "PermissionError" in str(err)
    assert isinstance(err.__cause__, OSError)
    assert "primary failure on a.md" in str(err.__cause__)


@pytest.mark.asyncio
async def test_write_all_rollback_continues_after_partial_failure(tmp_path: Path):
    """T3 best-effort: when field A's rollback fails, field B's rollback
    still runs and succeeds."""
    from trpc_agent_sdk.evaluation._target_prompt import _RollbackError

    p1 = _write(tmp_path / "a.md", "baseline-a")
    p2 = _write(tmp_path / "b.md", "baseline-b")
    p3 = _write(tmp_path / "c.md", "baseline-c")
    target = (
        TargetPrompt()
        .add_path("a", str(p1))
        .add_path("b", str(p2))
        .add_path("c", str(p3))
    )

    original_replace = os.replace
    state = {"n": 0}

    def _flaky_replace(src: str, dst: str) -> None:
        state["n"] += 1
        # forward: 1=a, 2=b, 3=c (fails -> rollback for [a, b])
        # rollback: 4=a-restore (fails), 5=b-restore (succeeds)
        if state["n"] == 3:
            raise OSError("primary failure on c")
        if state["n"] == 4:
            raise PermissionError("rollback a denied")
        return original_replace(src, dst)

    with mock.patch("os.replace", side_effect=_flaky_replace):
        with pytest.raises(_RollbackError) as excinfo:
            await target.write_all({"a": "new-a", "b": "new-b", "c": "new-c"})

    # Best-effort: b's rollback ran and succeeded.
    assert p2.read_text(encoding="utf-8") == "baseline-b"
    err = excinfo.value
    assert len(err.failures) == 1
    assert err.failures[0][0] == "a"


@pytest.mark.asyncio
async def test_write_all_rollback_uses_atomic_primitive(tmp_path: Path, monkeypatch):
    """T8: critical regression. Rollback restore path must go through
    _atomic_write_path (tmp + os.replace), not raw Path.write_text."""
    p1 = _write(tmp_path / "a.md", "baseline-a")
    p2 = _write(tmp_path / "b.md", "baseline-b")
    target = TargetPrompt().add_path("a", str(p1)).add_path("b", str(p2))

    rollback_calls: list[str] = []
    original_atomic = TargetPrompt._atomic_write_path

    def _spy_atomic(path: str, content: str) -> None:
        rollback_calls.append(path)
        return original_atomic(path, content)

    monkeypatch.setattr(TargetPrompt, "_atomic_write_path", staticmethod(_spy_atomic))

    original_replace = os.replace
    state = {"n": 0}

    def _flaky_replace(src: str, dst: str) -> None:
        state["n"] += 1
        if state["n"] == 2:
            raise OSError("simulated")
        return original_replace(src, dst)

    with mock.patch("os.replace", side_effect=_flaky_replace):
        with pytest.raises(OSError, match="simulated"):
            await target.write_all({"a": "new-a", "b": "new-b"})

    # forward writes for a + b (2 calls), then rollback restore for a (1 call) = 3.
    # If rollback used raw write_text, the third call would not appear.
    assert len(rollback_calls) == 3
    assert str(p1) in rollback_calls
    assert p1.read_text(encoding="utf-8") == "baseline-a"


@pytest.mark.asyncio
async def test_write_all_keyboard_interrupt_during_callback_still_rolls_back(tmp_path: Path):
    """KeyboardInterrupt is BaseException; except BaseException ensures
    rollback still runs for path fields when interrupted mid-callback."""
    p1 = _write(tmp_path / "a.md", "baseline-a")

    async def _read() -> str:
        return ""

    async def _write_fn(value: str) -> None:
        raise KeyboardInterrupt()

    target = (
        TargetPrompt()
        .add_path("a", str(p1))
        .add_callback("c", read=_read, write=_write_fn)
    )

    with pytest.raises(KeyboardInterrupt):
        await target.write_all({"a": "new-a", "c": "new-c"})

    assert p1.read_text(encoding="utf-8") == "baseline-a"
    leftover = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftover == []


@pytest.mark.asyncio
async def test_write_all_no_tmp_left_after_rollback(tmp_path: Path):
    """T7 extension: after forward fail + rollback success, no .tmp residue
    anywhere in the directory."""
    p1 = _write(tmp_path / "a.md", "baseline-a")
    p2 = _write(tmp_path / "b.md", "baseline-b")
    target = TargetPrompt().add_path("a", str(p1)).add_path("b", str(p2))

    original_replace = os.replace
    state = {"n": 0}

    def _flaky_replace(src: str, dst: str) -> None:
        state["n"] += 1
        if state["n"] == 2:
            raise OSError("simulated")
        return original_replace(src, dst)

    with mock.patch("os.replace", side_effect=_flaky_replace):
        with pytest.raises(OSError):
            await target.write_all({"a": "new-a", "b": "new-b"})

    leftover = sorted(f for f in os.listdir(tmp_path) if f.endswith(".tmp"))
    assert leftover == []
    assert p1.read_text(encoding="utf-8") == "baseline-a"
    assert p2.read_text(encoding="utf-8") == "baseline-b"
