# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SLO Runtime Control example 的优化器入口。

适用场景
--------
在 CI 流水线 / 夜间窗口等具有硬性时间和资源约束的环境下运行 prompt 优化，
需要"任何一个 SLO 触发都立刻停"的多重停止策略。本脚本演示同时启用 SDK
提供的 6 种 algorithm-level stop conditions，OR 语义抢闸。

这个文件做什么
--------------
1. 注册单字段 TargetPrompt（agent/prompts/system.md）
2. 定义 call_agent：用 _normalize_response 把 LLM 输出规范化为稳定 JSON
   字符串，使 final_response_avg_score 走 text exact 而非依赖 LLM judge
3. 调 AgentOptimizer.optimize；6 种 stop condition 阈值在 optimizer.json 中

怎么跑
------
1) 配 TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME
2) python examples/optimization/slo_runtime_control/run_optimization.py
3) 看 runs/<时间戳>/result.json 中的 stop_reason 字段，识别哪条 SLO 抢闸

接入自有业务时改哪里
--------------------
- optimizer.json 中 6 个 stop condition 阈值按业务 SLO 反推
  （详见 README §5 与 §8）
- agent/agent.py 改为业务 agent
- _normalize_response 按业务输出格式调整（业务非 JSON 输出可整体替换）
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt  # noqa: E402
from trpc_agent_sdk.runners import Runner  # noqa: E402
from trpc_agent_sdk.sessions import InMemorySessionService  # noqa: E402
from trpc_agent_sdk.types import Content, Part  # noqa: E402

from agent.agent import SYSTEM_PROMPT_PATH, create_agent  # noqa: E402


CONFIG_PATH = _HERE / "optimizer.json"
TRAIN_PATH = _HERE / "train.evalset.json"
VAL_PATH = _HERE / "val.evalset.json"
RUNS_DIR = _HERE / "runs"
APP_NAME = "slo_runtime_control_agent"


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _normalize_response(raw: str) -> str:
    """把 LLM 自由文本规范化成与 reference 完全一致的字符串形态。

    与 blackbox_cli / ci_integration 完全相同的规范化逻辑：让
    final_response_avg_score(text.match=exact) 直接走精确匹配，
    避免 LLM judge 引入额外不确定性与时间开销——这对运行时 SLO
    控制场景至关重要。
    """
    text = (raw or "").strip()
    if not text:
        return ""
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return text
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return text
    return json.dumps(parsed, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


async def call_agent(query: str) -> str:
    """框架回调：跑一次推理，输出经 _normalize_response 规范化。

    每个 case 一份独立的 Runner + InMemorySessionService，保证并发评测时
    session state 不互相污染。
    """
    root_agent = create_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)
    session_id = str(uuid.uuid4())
    user_id = "optimizer"
    await session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id, state={}
    )
    user_content = Content(role="user", parts=[Part.from_text(text=query)])

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=user_content
    ):
        if not event.is_final_response():
            continue
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.thought:
                continue
            if part.text:
                final_text += part.text
    return _normalize_response(final_text)


async def main() -> None:
    """组装 TargetPrompt + 调 AgentOptimizer.optimize。"""
    target = TargetPrompt().add_path("system_prompt", str(SYSTEM_PROMPT_PATH))

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    output_dir = RUNS_DIR / timestamp

    await AgentOptimizer.optimize(
        config_path=str(CONFIG_PATH),
        call_agent=call_agent,
        target_prompt=target,
        train_dataset_path=str(TRAIN_PATH),
        validation_dataset_path=str(VAL_PATH),
        output_dir=str(output_dir),
        update_source=False,
        verbose=1,
    )


if __name__ == "__main__":
    asyncio.run(main())
