#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Agent 与报告增强 Prompt 的安全合同测试。"""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.prompts import AGENT_INSTRUCTION, ENHANCEMENT_INSTRUCTION


def test_agent_instruction_locks_the_skill_tool_order_and_arguments() -> None:
    """验证 Agent Prompt 明确锁定 Skill 顺序、名称和一次性请求参数。"""

    load_position = AGENT_INSTRUCTION.index("skill_load")
    run_position = AGENT_INSTRUCTION.index("skill_run")

    assert load_position < run_position
    assert "code-review" in AGENT_INSTRUCTION
    assert "review_request_id" in AGENT_INSTRUCTION
    assert "一次" in AGENT_INSTRUCTION
    assert "不得加载其他 Skill" in AGENT_INSTRUCTION


def test_agent_instruction_forbids_untrusted_execution_inputs() -> None:
    """验证 Agent Prompt 禁止模型提供命令、路径、环境、diff 和代码。"""

    for forbidden_input in ("command", "路径", "环境变量", "diff", "代码"):
        assert forbidden_input in AGENT_INSTRUCTION
    assert "不得向 skill_run 提供" in AGENT_INSTRUCTION
    assert "manifest" in AGENT_INSTRUCTION
    assert "Filter" in AGENT_INSTRUCTION


def test_enhancement_instruction_cannot_change_finding_identity() -> None:
    """验证增强 Prompt 只能改进文本，不得改动或凭空生成 finding。"""

    assert "只增强" in ENHANCEMENT_INSTRUCTION
    for forbidden_action in ("不得新增", "删除", "合并", "重新分级"):
        assert forbidden_action in ENHANCEMENT_INSTRUCTION
    for protected_input in ("原始 diff", "代码", "环境变量", "凭据"):
        assert protected_input in ENHANCEMENT_INSTRUCTION
