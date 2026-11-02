"""One user turn with a single LLM call."""

from __future__ import annotations

import sys

from .._utils._session_bench import run_turns
from ...core import BenchProfile
from ._script_fixtures import make_turn_script

TURN_COUNT = 1
TURN_SCRIPT = make_turn_script(TURN_COUNT)
DESCRIPTION = ("1 个 user turn，1 次 LLM 调用，文本固定 1 chunk。"
               "指标：p50/p95 = 整个会话的耗时；memory = 本次采样中 tracemalloc 记录的峰值。")


def get_bench_profile() -> BenchProfile:
    return BenchProfile(supports_concurrency=True)


def main() -> int:
    return run_turns(TURN_SCRIPT, name="turn1", description=DESCRIPTION)


if __name__ == "__main__":
    sys.exit(main())
