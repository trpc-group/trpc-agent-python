"""Five LLM calls in a single user turn."""

from __future__ import annotations

import sys

from .._utils._session_bench import run_turns
from ...core import BenchProfile
from ._script_fixtures import make_loop_script
from ._tool_fixtures import make_add_tool

LLM_CALLS = 5
TOOL_ROUNDS = LLM_CALLS - 1
TURN_SCRIPT = make_loop_script(LLM_CALLS)
DESCRIPTION = ("1 个 user turn，5 次 LLM 调用，4 次 tool round，最终文本 5 chunks。"
               "指标：p50/p95 = 整次 turn 的耗时；memory = 本次采样中 tracemalloc 记录的峰值。")


def get_bench_profile() -> BenchProfile:
    return BenchProfile(supports_concurrency=True)


def main() -> int:
    return run_turns(
        TURN_SCRIPT,
        name="loop5",
        description=DESCRIPTION,
        tools_factory=make_add_tool,
    )


if __name__ == "__main__":
    sys.exit(main())
