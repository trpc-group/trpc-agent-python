"""Ten user turns with one LLM call each."""

from __future__ import annotations

import sys

from ._utils._session_bench import run_turns
from .. import BenchProfile
from ._script_fixtures import make_turn_script

TURN_COUNT = 10
TURN_SCRIPT = make_turn_script(TURN_COUNT)
DESCRIPTION = ("10 个 user turn，每个 turn 1 次 LLM 调用，每个 turn 的文本固定 1 chunk。"
               "指标：p50/p95 = 整个会话的耗时；memory = 本次采样中 tracemalloc 记录的峰值。")


def get_bench_profile() -> BenchProfile:
    return BenchProfile(supports_concurrency=True)


def main() -> int:
    return run_turns(TURN_SCRIPT, name="turn10", description=DESCRIPTION)


if __name__ == "__main__":
    sys.exit(main())
