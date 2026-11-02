"""run_bench command, environment construction, and skip/concurrency behavior."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from trpc_agent_bench import run_bench


@pytest.fixture(autouse=True)
def _clear_backend_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCH_REDIS_URL", raising=False)
    monkeypatch.delenv("BENCH_SQL_URL", raising=False)


def _run(
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[list[str], dict[str, object]]]:
    calls: list[tuple[list[str], dict[str, object]]] = []
    real_run = run_bench.subprocess.run

    def fake_run(command: list[str], **kwargs: object):
        if command[:3] == [sys.executable, "-m", "trpc_agent_bench.bench._runner"]:
            calls.append((command, kwargs))
            return run_bench.subprocess.CompletedProcess(command, 0)
        return real_run(command, **kwargs)

    monkeypatch.setattr(run_bench.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_bench.py", *argv])
    assert run_bench.main() == 0
    return calls


def _environments(calls: list[tuple[list[str], dict[str, object]]]) -> list[dict[str, object]]:
    return [kwargs["env"] for _, kwargs in calls]  # type: ignore[list-item]


def test_run_bench_builds_module_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _run(
        [
            "--targets",
            "target_pkg_d.agent_a,target_pkg_d.agent_b",
            "--baseline",
            "target_pkg_d.agent_a",
            "--benchmarks",
            "turn1,turn5",
            "--iterations",
            "3",
            "--results-dir",
            str(tmp_path / "results"),
        ],
        monkeypatch,
    )

    assert len(calls) == 4
    command, kwargs = calls[0]
    assert command == [sys.executable, "-m", "trpc_agent_bench.bench._runner"]
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["BENCH_TARGET"] == "target_pkg_d.agent_a"
    assert environment["BENCH_BENCHMARK"] == "trpc_agent_bench.bench._builtin.turn1"
    assert environment["BENCH_ITERATIONS"] == "3"
    assert environment["BENCH_CONCURRENCY"] == "1"
    assert environment["BENCH_RESULT_SUFFIX"] == ""
    assert environment["BENCH_RESULTS_DIR"] == str((tmp_path / "results" / "target_pkg_d.agent_a").resolve())
    pythonpath = environment["PYTHONPATH"]
    assert isinstance(pythonpath, str)
    assert str(run_bench.BENCH_DIR) in pythonpath.split(os.pathsep)

    builtin_benchmarks = [
        env["BENCH_BENCHMARK"] for env in _environments(calls)
        if str(env["BENCH_BENCHMARK"]).startswith("trpc_agent_bench.bench._builtin.")
    ]
    assert builtin_benchmarks == [
        "trpc_agent_bench.bench._builtin.turn1",
        "trpc_agent_bench.bench._builtin.turn5",
        "trpc_agent_bench.bench._builtin.turn1",
        "trpc_agent_bench.bench._builtin.turn5",
    ]

    summary = json.loads((tmp_path / "results" / "summary.json").read_text(encoding="utf-8"))
    assert summary["targets"] == ["target_pkg_d.agent_a", "target_pkg_d.agent_b"]
    assert summary["baseline"] == "target_pkg_d.agent_a"


def test_default_benchmarks_have_no_backend_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _run(
        [
            "--targets",
            "target_pkg_d.agent_a",
            "--iterations",
            "1",
            "--results-dir",
            str(tmp_path / "results"),
        ],
        monkeypatch,
    )

    names = [str(env["BENCH_BENCHMARK"]) for env in _environments(calls)]
    assert len(names) == 12
    assert all(not name.endswith(("_redis", "_sql")) for name in names)
    assert "trpc_agent_bench.bench._builtin.turn1" in names
    assert "trpc_agent_bench.bench._builtin.turn10" in names
    assert "trpc_agent_bench.bench._builtin.loop1" in names
    assert "trpc_agent_bench.bench._builtin.loop10" in names
    assert "trpc_agent_bench.bench._builtin.streaming" not in names


def test_unusable_benchmark_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = tmp_path / "bench_pkg_skip"
    module.mkdir()
    (module / "__init__.py").write_text("", encoding="utf-8")
    (module / "skipped.py").write_text(
        "from trpc_agent_bench.bench import BenchProfile\n"
        "def get_bench_profile():\n"
        "    return BenchProfile(usable=False, skip_reason='not available')\n"
        "def main():\n"
        "    return 0\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    calls = _run(
        [
            "--targets",
            "target_pkg_d.agent_a",
            "--benchmarks",
            "bench_pkg_skip.skipped",
            "--results-dir",
            str(tmp_path / "results"),
        ],
        monkeypatch,
    )

    assert calls == []
    captured = capsys.readouterr()
    assert "[skip] bench_pkg_skip.skipped (not available)" in captured.out


def test_concurrency_expands_only_supported_benchmarks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _run(
        [
            "--targets",
            "target_pkg_d.agent_a",
            "--benchmarks",
            "turn1,import_time",
            "--concurrency",
            "1,8",
            "--iterations",
            "1",
            "--results-dir",
            str(tmp_path / "results"),
        ],
        monkeypatch,
    )

    environments = _environments(calls)
    assert len(environments) == 3
    turn1_envs = [env for env in environments if env["BENCH_BENCHMARK"].endswith(".turn1")]
    import_envs = [env for env in environments if env["BENCH_BENCHMARK"].endswith(".import_time")]
    assert [(env["BENCH_CONCURRENCY"], env["BENCH_RESULT_SUFFIX"]) for env in turn1_envs] == [
        ("1", ""),
        ("8", "_c8"),
    ]
    assert [(env["BENCH_CONCURRENCY"], env["BENCH_RESULT_SUFFIX"]) for env in import_envs] == [
        ("1", ""),
    ]


def test_multiple_targets_require_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_bench.py",
            "--targets",
            "target_pkg_d.agent_a,target_pkg_d.agent_b",
            "--benchmarks",
            "turn1",
            "--iterations",
            "1",
            "--results-dir",
            str(tmp_path / "results"),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        run_bench.main()

    assert exc.value.code == 2
    assert "--baseline is required" in capsys.readouterr().err


def test_baseline_must_be_in_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_bench.py",
            "--targets",
            "target_pkg_d.agent_a,target_pkg_d.agent_b",
            "--baseline",
            "target_pkg_d.other",
            "--benchmarks",
            "turn1",
            "--iterations",
            "1",
            "--results-dir",
            str(tmp_path / "results"),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        run_bench.main()

    assert exc.value.code == 2
    assert "must be one of --targets" in capsys.readouterr().err


def test_invalid_concurrency_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_bench.py",
            "--targets",
            "target_pkg_d.agent_a",
            "--benchmarks",
            "turn1",
            "--concurrency",
            "0",
            "--iterations",
            "1",
            "--results-dir",
            str(tmp_path / "results"),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        run_bench.main()

    assert exc.value.code == 2
    assert "--concurrency values must be positive" in capsys.readouterr().err


def test_fixed_benchmarks_do_not_require_iterations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BENCH_ITERATIONS", raising=False)

    calls = _run(
        [
            "--targets",
            "target_pkg_d.agent_a",
            "--benchmarks",
            "import_time,memory_footprint",
            "--results-dir",
            str(tmp_path / "results"),
        ],
        monkeypatch,
    )

    assert len(calls) == 2
    assert all("BENCH_ITERATIONS" not in env for env in _environments(calls))


def test_iterations_required_for_selected_benchmarks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_bench.py",
            "--targets",
            "target_pkg_d.agent_a",
            "--benchmarks",
            "turn1",
            "--results-dir",
            str(tmp_path / "results"),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        run_bench.main()

    assert exc.value.code == 2
    assert "--iterations is required for: turn1" in capsys.readouterr().err


def test_missing_profile_defaults_to_usable_without_concurrency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = tmp_path / "bench_pkg_e"
    module.mkdir()
    (module / "__init__.py").write_text("", encoding="utf-8")
    (module / "no_hooks.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    calls = _run(
        [
            "--targets",
            "target_pkg_d.agent_a",
            "--benchmarks",
            "bench_pkg_e.no_hooks",
            "--concurrency",
            "2",
            "--iterations",
            "1",
            "--results-dir",
            str(tmp_path / "results"),
        ],
        monkeypatch,
    )

    assert len(calls) == 1
    environment = _environments(calls)[0]
    assert environment["BENCH_CONCURRENCY"] == "1"
    assert environment["BENCH_RESULT_SUFFIX"] == ""
    assert environment["BENCH_ITERATIONS"] == "1"


def test_preflight_import_sees_target_and_restores_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "bench_pkg_f"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "probe.py").write_text(
        "import os\n"
        "SEEN = os.environ.get('BENCH_TARGET')\n"
        "def main():\n"
        "    return 0\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delenv("BENCH_TARGET", raising=False)

    module = run_bench._load_benchmark_module("bench_pkg_f.probe", "target_pkg_d.agent_a")

    assert module.SEEN == "target_pkg_d.agent_a"
    assert os.environ.get("BENCH_TARGET") is None
