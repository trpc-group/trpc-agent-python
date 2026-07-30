# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""PR 阶段的质量守门测试：CI 闭环的"评测"端。

适用场景
--------
PR 触发的 CI 流水线运行此测试。任何 case 不通过都让 pytest exit code != 0
→ CI 红灯 → 阻止 PR 合并。

为什么不依赖 LLM judge
----------------------
CI 上要求快、稳、可重复。call_agent 的输出已经在 agent/agent.py 中被
_normalize_json 规范化为稳定 JSON 字符串，与 evalset 中 expected 字段
逐字符比对即可，无需再调一次 LLM 当裁判（速度更慢、判定不稳定、依赖
多一个外部服务）。

case 失败时框架抛 AssertionError，错误消息包含每条 case 的失败明细 JSON。
配合 pytest --junitxml=... 可输出标准 JUnit XML，GitHub Actions /
Tencent CI / 蓝盾流水线均原生支持解析展示。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent
_EXAMPLE_ROOT = _HERE.parent
_REPO_ROOT = _EXAMPLE_ROOT.parents[2]

# 让 example 目录里的 agent 包能被 import（pytest 默认 cwd 不一定是 example）。
for p in (_REPO_ROOT, _EXAMPLE_ROOT):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)


VAL_EVALSET = _EXAMPLE_ROOT / "data" / "val.evalset.json"
RESULT_DIR = _EXAMPLE_ROOT / "runs" / "pytest_eval"


@pytest.mark.asyncio
async def test_agent_meets_quality_bar() -> None:
    """所有 val case 必须 final_response 完全匹配，否则 CI 红灯。"""
    from trpc_agent_sdk.evaluation import AgentEvaluator
    from agent.agent import call_agent  # type: ignore

    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    await AgentEvaluator.evaluate(
        eval_dataset_file_path_or_dir=str(VAL_EVALSET),
        call_agent=call_agent,
        agent_name="api_summarizer",
        eval_result_output_dir=str(RESULT_DIR),
        print_detailed_results=True,
    )
