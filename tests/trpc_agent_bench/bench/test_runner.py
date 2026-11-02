"""Benchmark module loading and the subprocess entry point."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trpc_agent_bench.bench import _runner


def _write_module(root: Path, module: str, source: str) -> None:
    parts = module.split(".")
    for index in range(1, len(parts)):
        package = root.joinpath(*parts[:index])
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
    path = root.joinpath(*parts).with_suffix(".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_load_benchmark_returns_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_module(tmp_path, "bench_pkg_a.my_bench", "def main():\n    return 7\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    entry = _runner.load_benchmark("bench_pkg_a.my_bench")

    assert entry() == 7


def test_runner_awaits_async_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_module(
        tmp_path,
        "bench_pkg_async.async_bench",
        "async def main():\n    return 9\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("BENCH_BENCHMARK", "bench_pkg_async.async_bench")

    assert _runner.main() == 9


def test_load_benchmark_requires_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_module(tmp_path, "bench_pkg_b.no_main", "X = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(RuntimeError, match="does not define main"):
        _runner.load_benchmark("bench_pkg_b.no_main")


def test_load_benchmark_sets_description(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_module(
        tmp_path,
        "bench_pkg_c.with_description",
        'DESCRIPTION = "bench description"\ndef main():\n    return 7\n',
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delenv("BENCH_DESCRIPTION", raising=False)

    entry = _runner.load_benchmark("bench_pkg_c.with_description")

    assert entry() == 7
    assert os.environ["BENCH_DESCRIPTION"] == "bench description"


def test_runner_requires_benchmark_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCH_BENCHMARK", raising=False)

    with pytest.raises(RuntimeError, match="BENCH_BENCHMARK is not set"):
        _runner.main()
