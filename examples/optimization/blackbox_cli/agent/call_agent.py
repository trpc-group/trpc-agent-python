# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Blackbox CLI 的 call_agent 实现：subprocess 调外部 CLI 进程。

适用场景
--------
当业务 agent 是外部命令行工具时，本文件作为优化器与 CLI 之间的适配层。
SDK 不持有 CLI 的 LLM client / Runner，仅通过 subprocess 调用，整个优化
流程与 CLI 内部实现完全解耦。

核心设计
--------
1. asyncio.create_subprocess_exec 启动子进程：query 作 argv 传入，避免
   shell 转义问题。子进程独立进程不受 SDK 内部事件循环约束影响。
2. _build_cli_env 把通用 TRPC_AGENT_* 三件套映射成 CLI 期望的
   TRPC_CLAUDECODE_* 三件套，并附加 GLM-5.1 推荐的 auto-compact 阈值。
   业务方无需为 CLI 单独配置 OAuth 或 ANTHROPIC_API_KEY。
3. _normalize_response 用 json.dumps(sort_keys, separators) 把 LLM 自由
   文本转换为唯一字符串形态，使 final_response_avg_score(text.match=exact)
   可直接走精确匹配，CI 上无需 LLM judge。
4. CLI_TIMEOUT_SEC 防止单次 CLI 卡死拖垮整轮评估。

接入自有 CLI 时改哪里
---------------------
- CLI_BINARY: 替换为业务 CLI 可执行路径
- _run_cli 中的 argv 数组: 按业务 CLI 协议改造（argv 传 query / stdin
  传 query / --query xxx 等）
- _build_cli_env: 改为业务 CLI 期望的环境变量；如业务 CLI 已有 OAuth
  流程，整体删除该映射并提示用户先登录
- _normalize_response: 按业务 CLI 输出格式调整规范化逻辑
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent.parent / "workspace"
CLI_BINARY = "trpc-claudecode"
CLI_TIMEOUT_SEC = 90.0


def _build_cli_env() -> dict[str, str]:
    """把通用 TRPC_AGENT_* 三件套映射成 CLI 期望的 TRPC_CLAUDECODE_* 三件套。

    同时注入 GLM-5.1 推荐的 auto-compact 阈值（参考 trpc-claudecode 官方说明）。
    用户只需配通用三件套，无需为 CLI 单独配 OAuth 或 ANTHROPIC_API_KEY。
    """
    env = dict(os.environ)
    base_url = env.get("TRPC_AGENT_BASE_URL")
    api_key = env.get("TRPC_AGENT_API_KEY")
    model_name = env.get("TRPC_AGENT_MODEL_NAME")
    if not (base_url and api_key and model_name):
        raise RuntimeError(
            "TRPC_AGENT_BASE_URL / TRPC_AGENT_API_KEY / TRPC_AGENT_MODEL_NAME "
            "must be set so they can be forwarded to trpc-claudecode."
        )
    env["TRPC_CLAUDECODE_BASE_URL"] = base_url
    env["TRPC_CLAUDECODE_API_KEY"] = api_key
    env["TRPC_CLAUDECODE_MODEL"] = model_name
    env.setdefault("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "165000")
    env.setdefault("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "85")
    return env


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _normalize_response(raw: str) -> str:
    """把 CLI stdout 规范化成稳定 JSON 字符串。

    步骤：
    1. 用正则定位首个 {...} 块（兼容 LLM 偶尔在 JSON 前后多吐字符的情况）
    2. json.loads + json.dumps(sort_keys, separators) 消除空格 / key 顺序差异
    3. 解析失败时原样返回 stripped stdout（让 metric 看到 "garbage" → 0 分）

    经过本函数后 baseline 与候选 prompt 的输出对齐到唯一字符串形态，
    final_response_avg_score(text.match=exact) 可直接逐字符比对。
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


async def _run_cli(query: str) -> str:
    """启动 CLI 子进程，喂 query，返回 stdout（带 timeout 保护）。

    超时后强制 kill 子进程并抛 RuntimeError，避免单次 CLI 卡死拖垮整轮评估。
    """
    cmd = [
        CLI_BINARY,
        "--print",
        "--add-dir",
        str(WORKSPACE_DIR),
        "--dangerously-skip-permissions",
        query,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_build_cli_env(),
        cwd=str(WORKSPACE_DIR),
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=CLI_TIMEOUT_SEC
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(
            f"trpc-claudecode timed out after {CLI_TIMEOUT_SEC}s on query={query!r}"
        )

    if proc.returncode != 0:
        raise RuntimeError(
            f"trpc-claudecode exited with code {proc.returncode}; "
            f"stderr={stderr_b.decode('utf-8', 'replace')[:400]}"
        )
    return stdout_b.decode("utf-8", "replace")


async def call_agent(query: str) -> str:
    """框架回调：把 query 透传给外部 CLI 黑盒，返回规范化后的输出。"""
    raw = await _run_cli(query)
    return _normalize_response(raw)
