"""Naming helpers shared by built-in benchmarks."""

from __future__ import annotations

import pytest

from trpc_agent_bench.bench._builtin._utils._common import concurrency_label
from trpc_agent_bench.bench._builtin._utils._common import new_session_id
from trpc_agent_bench.bench._builtin._utils._common import result_name
from trpc_agent_bench.bench._builtin._utils._common import result_suffix


def test_new_session_id_is_unique() -> None:
    values = {new_session_id() for _ in range(5)}

    assert len(values) == 5


def test_result_name_uses_run_bench_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCH_CONCURRENCY", "1")
    monkeypatch.delenv("BENCH_RESULT_SUFFIX", raising=False)
    assert result_name("turn5") == "turn5"
    assert result_suffix() == ""
    assert concurrency_label() == ""

    monkeypatch.setenv("BENCH_CONCURRENCY", "8")
    assert result_name("turn5") == "turn5_c8"
    assert result_suffix() == "_c8"
    assert concurrency_label() == "concurrency=8"

    # run_bench controls the suffix explicitly, including the no-suffix case.
    monkeypatch.setenv("BENCH_RESULT_SUFFIX", "")
    assert result_name("turn5") == "turn5"
