# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""被优化的 agent：三个 prompt 字段(router/system/skill) + 双后端(real/fake)。"""

from __future__ import annotations

from .orchestrator import (
    ROUTER_PROMPT_PATH,
    SKILL_PROMPT_PATH,
    SYSTEM_PROMPT_PATH,
)

# TargetPrompt 字段名 -> prompt 文件路径。pipeline 各阶段共用这一份映射：
# real 模式喂给 AgentOptimizer 的 TargetPrompt，fake 模式喂给确定性求解器。
PROMPT_PATHS = {
    "router": ROUTER_PROMPT_PATH,
    "system_prompt": SYSTEM_PROMPT_PATH,
    "skill": SKILL_PROMPT_PATH,
}

__all__ = [
    "PROMPT_PATHS",
    "ROUTER_PROMPT_PATH",
    "SYSTEM_PROMPT_PATH",
    "SKILL_PROMPT_PATH",
]
