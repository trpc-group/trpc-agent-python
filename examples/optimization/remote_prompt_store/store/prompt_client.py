# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""prompt KV 的 async 访问层 —— TargetPrompt.add_callback 期望的实现。

适用场景
--------
本文件是 add_callback 接入远端配置中心的**核心模板**。函数签名严格匹配
add_callback 的协议：read 是 async () -> str，write 是 async (str) -> None。

namespace 隔离设计
------------------
- production：业务线上读取的 prompt，**永远不被优化器写入**
- sandbox：优化器读 / 写的工作 namespace；update_source=False 时优化器
  在收尾阶段把 sandbox 自动回滚到 baseline 快照

接入自有配置中心时改哪里
------------------------
保持四个公开 async 函数的签名不变，把内部实现从 FakeKVStore 替换为
业务真实 SDK 调用：

    async def read_sandbox_prompt() -> str:
        return await your_config_sdk.get(namespace="sandbox", key="system_prompt")

    async def write_sandbox_prompt(value: str) -> None:
        await your_config_sdk.put(namespace="sandbox", key="system_prompt", value=value)

run_optimization.py 中 add_callback 调用无需修改。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .fake_kv_store import FakeKVStore


# 演示用：本地 JSON 文件持久化的 KV。真实业务里这一层换成配置中心 SDK
# 的全局 client（如 _CFG_CLIENT = your_sdk.Client(...)），不再依赖本文件。
_STORE_PATH = Path(__file__).resolve().parent / "store.json"
_KV = FakeKVStore(_STORE_PATH)

PROMPT_KEY_PRODUCTION = "system_prompt:production"
PROMPT_KEY_SANDBOX = "system_prompt:sandbox"


async def read_sandbox_prompt() -> str:
    """从沙箱 namespace 读 prompt——优化器评测候选时调用。

    add_callback 期望此函数无参数返回当前 prompt 文本。
    """
    # 真实场景下走网络请求；这里 await asyncio.sleep(0) 模拟一次 await
    # 切点，让协程在 KV 调用处可被调度。
    await asyncio.sleep(0)
    return _KV.read(PROMPT_KEY_SANDBOX)


async def write_sandbox_prompt(value: str) -> None:
    """写入沙箱 namespace——优化器落候选 / 收尾回滚 baseline 都走这里。

    add_callback 期望此函数接受新 prompt 文本，无返回值。
    实现需保证幂等性：优化器收尾时会再次调本函数把 sandbox 写回 baseline，
    不幂等的写入可能导致回滚失败。
    """
    await asyncio.sleep(0)
    _KV.write(PROMPT_KEY_SANDBOX, value)


async def read_production_prompt() -> str:
    """读生产 namespace 的 prompt——首次接入时用它初始化沙箱。"""
    await asyncio.sleep(0)
    return _KV.read(PROMPT_KEY_PRODUCTION)


def reset_store(production_prompt: str) -> None:
    """演示用：把 KV 初始化到 production / sandbox 都为给定 prompt 的状态。

    真实业务下不应调本函数——业务的生产 namespace 由 ops 维护，
    优化器只关心读 / 写沙箱。
    """
    _KV.write(PROMPT_KEY_PRODUCTION, production_prompt)
    _KV.write(PROMPT_KEY_SANDBOX, production_prompt)
