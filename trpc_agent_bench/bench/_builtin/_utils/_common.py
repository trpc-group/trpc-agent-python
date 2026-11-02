"""Internal naming helpers shared by built-in benchmarks."""

from __future__ import annotations

import uuid

from ... import get_concurrency


def new_session_id() -> str:
    return str(uuid.uuid4())


def result_suffix() -> str:
    """Return the suffix ``run_bench`` assigned to this process."""

    import os

    suffix = os.environ.get("BENCH_RESULT_SUFFIX")
    if suffix is not None:
        return suffix
    value = get_concurrency()
    return f"_c{value}" if value > 1 else ""


def result_name(base: str) -> str:
    return f"{base}{result_suffix()}"


def concurrency_label() -> str:
    value = get_concurrency()
    return f"concurrency={value}" if value > 1 else ""
