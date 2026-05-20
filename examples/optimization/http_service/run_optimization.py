# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""HTTP Service example 的优化器入口（客户端进程）。

适用场景
--------
业务 agent 已作为独立 HTTP 服务在线运行，希望对其 prompt 做自动优化但
不想停服、不想改服务代码。本脚本作为优化器以纯客户端身份接入服务，
通过磁盘 prompt 文件实现优化器与服务的解耦。

这个文件做什么
--------------
1. 启动前同步健康检查，服务不通即 fail-fast
2. 注册 service/prompts/system.md 为 TargetPrompt
3. 在 call_agent 中用 async with httpx.AsyncClient 即用即关
4. 调 AgentOptimizer.optimize 跑 GEPA 反思循环

怎么跑
------
终端 A: python examples/optimization/http_service/service/server.py
终端 B（本脚本）:
  1) 配 TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME
  2) python examples/optimization/http_service/run_optimization.py
  3) 看 runs/<时间戳>/summary.txt

接入自有 HTTP 服务时改哪里
--------------------------
- SERVICE_BASE_URL / CHAT_URL / HEALTH_URL : 改为业务服务地址
- call_agent 内 payload / 响应字段 : 按业务 schema 调整
- SYSTEM_PROMPT_PATH : 指向服务进程实际读取的 prompt 文件
- REQUEST_TIMEOUT : 按业务首次推理耗时上调
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import httpx


_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt


CONFIG_PATH = _HERE / "optimizer.json"
TRAIN_PATH = _HERE / "train.evalset.json"
VAL_PATH = _HERE / "val.evalset.json"
RUNS_DIR = _HERE / "runs"
SYSTEM_PROMPT_PATH = _HERE / "service" / "prompts" / "system.md"

SERVICE_BASE_URL = "http://127.0.0.1:8767"
HEALTH_URL = f"{SERVICE_BASE_URL}/health"
CHAT_URL = f"{SERVICE_BASE_URL}/chat"

# 单次 HTTP 请求超时（秒）。HTTP 服务内部需走一次完整 LLM 推理，
# 首次冷启动后单次耗时通常 ~10-30s，留 120s 足够缓冲。
REQUEST_TIMEOUT = 120.0


def _ensure_service_alive_sync() -> None:
    """同步健康检查：服务不通立刻报错。"""
    try:
        resp = httpx.get(HEALTH_URL, timeout=5.0)
        resp.raise_for_status()
    except Exception as ex:
        raise RuntimeError(
            f"HTTP service at {SERVICE_BASE_URL} is not reachable: {ex}\n"
            "Please start the service first:\n"
            "  python examples/optimization/http_service/service/server.py"
        ) from ex


async def call_agent(query: str) -> str:
    """框架回调：把 query 发给 HTTP 服务，返回 agent 的最终回答。

    每次调用新建 AsyncClient 并用 async with 在退出时自动关闭。这是
    httpx 官方推荐用法（GitHub Discussion #2959）：AsyncClient 的连接
    池绑定到首次使用时所在的事件循环，不支持跨事件循环复用。每次
    新建 client 仅增加 ~10ms 建连开销，相对单次 LLM 推理耗时可忽略。
    """
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(CHAT_URL, json={"query": query})
        resp.raise_for_status()
        return resp.json()["final_text"]


async def main() -> None:
    """组装 TargetPrompt + 调 AgentOptimizer.optimize。"""
    _ensure_service_alive_sync()

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
        # update_source=False：源 prompt 文件保持不变，最优候选只写到
        # output_dir/best_prompts/。候选由人工 review 后再落盘
        # （或参见 ci_integration/ example）。
        update_source=False,
        # verbose: 0 静默 / 1 进度面板 / 2 加 gepa 内部诊断日志
        verbose=1,
    )


if __name__ == "__main__":
    asyncio.run(main())
