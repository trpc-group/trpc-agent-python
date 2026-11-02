"""Run every (target module, benchmark module) pair in a fresh process."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .bench._profile import BenchProfile

PACKAGE_DIR = Path(__file__).resolve().parent
BENCH_DIR = PACKAGE_DIR.parent
DEFAULT_BENCHMARKS = (
    "import_time",
    "instantiate_agent",
    "instantiate_agent_with_tools",
    "turn1",
    "turn5",
    "turn10",
    "loop1",
    "loop5",
    "loop10",
    "tools",
    "memory_footprint",
    "tools_parallel",
)
BUILTIN_BENCHMARK_PREFIX = "trpc_agent_bench.bench._builtin."


def _parse_list(value: str, default: tuple[str, ...]) -> list[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or list(default)


def _resolve_benchmark(name: str) -> str:
    """Built-in short names expand to modules; custom names stay as-is."""

    return name if "." in name else f"{BUILTIN_BENCHMARK_PREFIX}{name}"


def _load_benchmark_module(module_path: str, target: str):
    # Custom benchmark modules may import their target at module scope. Preflight
    # imports happen in this process, so give them a valid BENCH_TARGET and
    # restore the caller's value afterwards.
    previous_target = os.environ.get("BENCH_TARGET")
    os.environ["BENCH_TARGET"] = target
    try:
        return importlib.import_module(module_path)
    except ImportError as exc:
        raise RuntimeError(f"failed to import benchmark module {module_path!r}: {exc}", ) from exc
    finally:
        if previous_target is None:
            os.environ.pop("BENCH_TARGET", None)
        else:
            os.environ["BENCH_TARGET"] = previous_target


def _bench_profile(module: Any) -> BenchProfile:
    getter = getattr(module, "get_bench_profile", None)
    return getter() if getter is not None else BenchProfile()


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"must be an integer: {raw!r}") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _parse_concurrency(raw: str) -> list[int]:
    values = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            value = int(item)
        except ValueError as exc:
            raise ValueError(f"--concurrency must contain integers: {item!r}") from exc
        if value <= 0:
            raise ValueError("--concurrency values must be positive")
        values.append(value)
    if not values:
        raise ValueError("--concurrency must contain at least one positive integer")
    return sorted(set(values))


def _pythonpath() -> str:
    entries = [str(BENCH_DIR), str(Path.cwd())]
    existing = os.environ.get("PYTHONPATH")
    if existing:
        entries.append(existing)
    return os.pathsep.join(dict.fromkeys(entries))


def _collect(
    results_root: Path,
    targets: list[str],
    baseline: str | None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for target in targets:
        for path in sorted((results_root / target).glob("*.json")):
            results.append(json.loads(path.read_text(encoding="utf-8")))
    return {
        "results_dir": str(results_root),
        "targets": targets,
        "baseline": baseline,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the trpc-agent benchmark suite.")
    parser.add_argument(
        "--targets",
        required=True,
        help="comma-separated target module paths, e.g. llmagent.base",
    )
    parser.add_argument(
        "--benchmarks",
        default="",
        help=("comma-separated built-in names or custom benchmark module paths; "
              "defaults to the built-in suite"),
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="target module path used as the comparison baseline; required for multiple targets",
    )
    parser.add_argument(
        "--concurrency",
        default="1",
        help="comma-separated concurrency values, e.g. 1,8",
    )
    parser.add_argument(
        "--iterations",
        type=_positive_int,
        default=None,
        help=("iteration count for benchmarks that require it; "
              "required when any selected benchmark requires iterations"),
    )
    parser.add_argument(
        "--results-dir",
        default=None,
        help="results root, default: ./results",
    )
    args = parser.parse_args()

    targets = _parse_list(args.targets, ())
    if not targets:
        parser.error("--targets must list at least one target module")
    baseline = args.baseline.strip() if args.baseline else None
    if len(targets) > 1:
        if not baseline:
            parser.error("--baseline is required when --targets lists more than one target")
        if baseline not in targets:
            parser.error(f"--baseline must be one of --targets: {baseline!r}")
    elif baseline is not None and baseline != targets[0]:
        parser.error("--baseline must match the only target when --targets has one entry")

    try:
        concurrency_values = _parse_concurrency(args.concurrency)
    except ValueError as exc:
        parser.error(str(exc))

    raw_benchmarks = args.benchmarks.strip()
    if raw_benchmarks:
        benchmark_names = _parse_list(raw_benchmarks, ())
    else:
        benchmark_names = list(DEFAULT_BENCHMARKS)

    benchmarks = []
    for name in benchmark_names:
        module_path = _resolve_benchmark(name)
        try:
            module = _load_benchmark_module(module_path, targets[0])
        except RuntimeError as exc:
            parser.error(str(exc))
        profile = _bench_profile(module)
        if not profile.usable:
            reason = f" ({profile.skip_reason})" if profile.skip_reason else ""
            print(f"[skip] {name}{reason}", flush=True)
            continue
        benchmarks.append((name, module_path, module, profile))

    needs_iterations = [name for name, _, _, profile in benchmarks if profile.requires_iterations]
    if needs_iterations and args.iterations is None:
        parser.error("--iterations is required for: " + ", ".join(needs_iterations), )

    results_root = (Path(args.results_dir) if args.results_dir else Path("results")).resolve()

    for target in targets:
        for name, module_path, module, profile in benchmarks:
            supports_concurrency = profile.supports_concurrency
            run_concurrency = concurrency_values if supports_concurrency else [1]
            for current in run_concurrency:
                environment = os.environ.copy()
                environment["BENCH_TARGET"] = target
                environment["BENCH_BENCHMARK"] = module_path
                environment["BENCH_RESULTS_DIR"] = str(results_root / target)
                environment["BENCH_CONCURRENCY"] = str(current)
                result_suffix = f"_c{current}" if supports_concurrency and current > 1 else ""
                environment["BENCH_RESULT_SUFFIX"] = result_suffix
                environment["PYTHONPATH"] = _pythonpath()
                if args.iterations is not None:
                    environment["BENCH_ITERATIONS"] = str(args.iterations)
                command = [sys.executable, "-m", "trpc_agent_bench.bench._runner"]
                suffix = f" c={current}" if supports_concurrency and current > 1 else ""
                print(f"[bench] {target} / {name}{suffix}", flush=True)
                subprocess.run(command, env=environment, check=True)

    summary = _collect(results_root, targets, baseline)
    results_root.mkdir(parents=True, exist_ok=True)
    (results_root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    from .bench._report import build_report

    build_report(results_root, targets, baseline)
    return 0


if __name__ == "__main__":
    sys.exit(main())
