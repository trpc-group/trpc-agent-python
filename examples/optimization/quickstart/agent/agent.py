# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""小学算术应用题求解 agent —— Quickstart 专用。

适用场景
--------
本文件是 Quickstart example 的 agent 实现。它演示一个被 GEPA 优化的 agent
最常见的写法：用一个工厂函数 create_agent()，每次调用都从磁盘重读 prompt
文件再构建 LlmAgent，让 GEPA 写入的新候选立即生效。

这个文件做什么
--------------
- 暴露 SYSTEM_PROMPT_PATH / SKILL_PATH（被 run_optimization.py 注册到 TargetPrompt）
- 提供 create_agent() 工厂函数（被 call_agent 在每次推理时调用）

为什么 prompt 拆成两个文件
--------------------------
两个文件扮演不同角色，同时被 GEPA 优化：

  system.md  (key="system_prompt")
      定义 agent 的角色定位和输出格式约束。
      baseline 故意写"只输出最终答案"——与 skill.md 的"展开思路"冲突。

  skill.md   (key="skill")
      描述解题方法论，要求 agent 展开推理过程。

冲突是刻意设计：让 GEPA 必须识别矛盾、改写其中至少一个文件，才能让两条
metric 同时通过。这样能直观看到反思机制的价值。

两个文件按以下格式拼合：
    {system.md 内容}\n\n## 解题方法\n{skill.md 内容}

为什么每次都重新构建 agent，不复用实例
--------------------------------------
1. GEPA 在轮次之间会修改 prompt 文件；复用实例会用到旧 prompt
2. 并发 case 评测时每次独立构建更安全，无共享状态
3. LlmAgent 构建本身很轻（不涉及 LLM 调用），开销可忽略
"""

from pathlib import Path

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.types import GenerateContentConfig

from .config import get_model_config


# 两个 prompt 文件的绝对路径（run_optimization.py 把它们注册成 TargetPrompt）
SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"
SKILL_PATH = Path(__file__).parent / "prompts" / "skill.md"


def _create_model() -> LLMModel:
    """构建 OpenAI 兼容的 chat 模型实例。

    凭据从环境变量读取（见 config.py），缺任何一个都会 fail-fast。
    """
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)


def _read_instruction() -> str:
    """从两个 prompt 文件拼合完整 instruction。

    每次调用都重读磁盘，确保 GEPA 写入的新候选立即生效；分隔符 "## 解题方法"
    让拼合后的文本仍保持两块内容的边界，便于人类和 reflection_lm 阅读。
    """
    system = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    skill = SKILL_PATH.read_text(encoding="utf-8").strip()
    return f"{system}\n\n## 解题方法\n{skill}"


def _create_agent_with_prompts(instruction: str) -> LlmAgent:
    """LlmAgent 构建公共逻辑——给定 instruction，返回 agent 实例。

    把"读 prompt"和"构建 agent"分开，方便测试时直接传入字符串而不必依赖磁盘。
    """
    return LlmAgent(
        name="math_word_problem_agent",
        description=(
            "小学算术应用题求解 agent。system prompt 与 skill prompt 由 GEPA "
            "反思机制联合优化。"
        ),
        model=_create_model(),
        instruction=instruction,
        generate_content_config=GenerateContentConfig(
            temperature=0.2,
            top_p=0.9,
            max_output_tokens=2048,
        ),
    )


def create_agent() -> LlmAgent:
    """构建一个使用当前磁盘 prompt 的新 LlmAgent 实例。

    call_agent 在每次推理时调用此函数。
    """
    return _create_agent_with_prompts(_read_instruction())
