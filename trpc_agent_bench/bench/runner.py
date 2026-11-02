"""Run one benchmark module by its import path."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import sys
from typing import Any
from typing import Callable


def load_benchmark(module_path: str) -> Callable[[], Any]:
    """Import a benchmark module and return its ``main`` entry point."""

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise RuntimeError(f"failed to import benchmark module {module_path!r}: {exc}") from exc
    entry = getattr(module, "main", None)
    if entry is None:
        raise RuntimeError(f"benchmark module {module_path!r} does not define main()")
    os.environ["BENCH_DESCRIPTION"] = str(getattr(module, "DESCRIPTION", ""))
    return entry


def main() -> int:
    module_path = os.environ.get("BENCH_BENCHMARK")
    if not module_path:
        raise RuntimeError("BENCH_BENCHMARK is not set")
    result = load_benchmark(module_path)()
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    return result


if __name__ == "__main__":
    sys.exit(main())
