# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""模拟"业务方已编排好的多 agent 链路"。

链路形态::

    用户问题 → router → (fact_agent 或 math_agent) → summarizer → 最终答复

prompt 热加载约束
-----------------
每个 sub-agent 在每次被调用时必须重读自己的 prompt 文件——优化器通过
TargetPrompt.add_path 把候选 prompt 写入对应文件后，下一次 invoke_pipeline
调用各 sub-agent 自动用最新 prompt，无需重启。

接入自有链路时改哪里
--------------------
真实业务下整体替换本文件为业务链路代码：
- 每个 sub-agent 可以是不同进程 / 服务 / 框架
- prompt 通常通过配置中心（不是本地文件）下发；本文件 Path.read_text 换成
  配置中心 SDK 调用即可，链路骨架不变
- 主入口 invoke_pipeline(query) -> str 的签名保持不变，被 call_agent 调用
"""

from __future__ import annotations

import uuid
from pathlib import Path

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part

from .config import get_model_config


_PROMPTS_DIR = Path(__file__).parent / "prompts"
ROUTER_PROMPT_PATH = _PROMPTS_DIR / "router.md"
FACT_AGENT_PROMPT_PATH = _PROMPTS_DIR / "fact_agent.md"
MATH_AGENT_PROMPT_PATH = _PROMPTS_DIR / "math_agent.md"
SUMMARIZER_PROMPT_PATH = _PROMPTS_DIR / "summarizer.md"

APP_NAME = "multi_agent_pipeline_demo"


def _create_sub_agent(name: str, prompt_path: Path) -> LlmAgent:
    """构造一个 sub-agent，instruction 从对应文件现读现用。

    每次调用都重读磁盘——这是优化器写入新候选后能立即生效的关键。
    """
    api_key, base_url, model_name = get_model_config()
    return LlmAgent(
        name=name,
        description=f"Pipeline sub-agent {name}",
        model=OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url),
        instruction=prompt_path.read_text(encoding="utf-8").strip(),
        generate_content_config=GenerateContentConfig(
            temperature=0.2,
            top_p=0.9,
            max_output_tokens=1024,
        ),
    )


async def _run_one(agent: LlmAgent, user_text: str) -> str:
    """跑一个 sub-agent 拿最终回答。每次新建 Runner / Session 给本 case 独立 state。"""
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)
    session_id = str(uuid.uuid4())
    user_id = "pipeline"
    await session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id, state={},
    )
    user_content = Content(role="user", parts=[Part.from_text(text=user_text)])

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=user_content,
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
    return final_text.strip()


async def invoke_pipeline(query: str) -> str:
    """把 query 跑过整条链路，返回最终答复文本。

    流程：
      1. router 决定走 fact 还是 math 分支
      2. 对应分支 sub-agent 给出中间答复
      3. summarizer 把中间答复整理成最终答复

    每个 sub-agent 都重新构建（在 _create_sub_agent 内重读 prompt 文件），
    保证优化器写入候选后下一次调用即生效。
    """
    # 1. router：根据问题类型输出 fact / math 分类标签
    router = _create_sub_agent("router", ROUTER_PROMPT_PATH)
    router_out = await _run_one(
        router,
        f"用户问题：{query}\n\n请只输出 fact 或 math 这两个词中的一个。",
    )
    branch = "math" if "math" in router_out.lower() else "fact"

    # 2. 分支 sub-agent：根据 router 决策选 fact_agent 或 math_agent
    if branch == "math":
        branch_agent = _create_sub_agent("math_agent", MATH_AGENT_PROMPT_PATH)
    else:
        branch_agent = _create_sub_agent("fact_agent", FACT_AGENT_PROMPT_PATH)
    intermediate = await _run_one(branch_agent, query)

    # 3. summarizer：把中间结果整理为最终答复
    summarizer = _create_sub_agent("summarizer", SUMMARIZER_PROMPT_PATH)
    final_text = await _run_one(
        summarizer,
        f"用户问题：{query}\n\n上游 agent 给出的中间结果：{intermediate}\n\n"
        "请整理后呈现最终答复。",
    )
    return final_text
