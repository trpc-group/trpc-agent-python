# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Code Review Agent - SDK Agent 入口（Critical 2 修复：真用 skill_load/skill_run）

严格照抄 skills_with_container 示例的 Runner + SkillToolSet 用法：
- create_skill_tool_set() 返回 (SkillToolSet, SkillRepository)
- LlmAgent(tools=[skill_tool_set], skill_repository=repository)
- Agent 通过 skill_load + skill_run 在隔离 workspace 执行脚本
"""
import asyncio
import sys
import uuid
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import AnthropicModel

# 加载环境变量
load_dotenv()

# 技能根目录：本项目的 skills/ 目录
_SKILLS_ROOT = Path(__file__).parent / "skills"


def _create_skill_tool_set():
    """创建 SkillToolSet（照抄 skills_with_container 示例）

    Returns:
        (SkillToolSet, SkillRepository): 技能工具集和技能仓库
    """
    from trpc_agent_sdk.skills import SkillToolSet, create_default_skill_repository

    # 技能路径（本项目 skills/ 目录）
    skill_paths = [str(_SKILLS_ROOT)]

    # 创建技能仓库
    repository = create_default_skill_repository(skill_paths)

    # 创建技能工具集
    tool_kwargs = {
        "save_as_artifacts": True,
        "omit_inline_content": False,
    }

    tool_set = SkillToolSet(repository=repository, run_tool_kwargs=tool_kwargs)

    return tool_set, repository


def _create_model(model_name: Optional[str] = None):
    """创建 LLM 模型"""
    if model_name is None:
        model_name = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    return AnthropicModel(model_name)


class CodeReviewAgent:
    """代码评审 Agent（Critical 2 修复：集成 SkillToolSet）"""

    def __init__(self, model_name: Optional[str] = None):
        """初始化 Code Review Agent

        Args:
            model_name: 模型名称，如果为 None 则从环境变量读取
        """
        # 创建技能工具集（Critical 2 修复：真用 SkillToolSet）
        skill_tool_set, skill_repository = _create_skill_tool_set()

        # 创建 LlmAgent（集成 skill_tool_set 和 skill_repository）
        self._agent = LlmAgent(
            name="code_review_agent",
            model=_create_model(model_name),
            instruction="""你是一个专业的代码评审 Agent。

你的任务是：
1. 分析代码变更（git diff）
2. 使用 code-review skill 执行静态代码检查：
   - 先调用 skill_load("code-review") 加载技能
   - 再调用 skill_run 执行 static_review.py 和 diff_summary.py
3. 根据检查结果，按 8 类规则进行安全/质量审查：
   - 安全规则（SQL 注入、硬编码密钥、不安全随机数）
   - 异常处理（async/await、异常捕获范围）
   - 资源泄漏（文件、数据库、网络连接）
   - 数据库生命周期（事务、连接池、N+1 查询）
   - 敏感信息保护（日志、错误响应、配置文件）
   - 测试覆盖度（单元测试、边界条件、异常场景）

请提供结构化的评审报告，包含：
- 变更概览
- 潜在问题列表（按严重程度排序）
- 改进建议（优先级排序）

重要：使用 skill_load 和 skill_run 工具来执行具体的检查。
""",
            tools=[skill_tool_set],  # Critical 2 修复：传入 skill_tool_set
            skill_repository=skill_repository,  # Critical 2 修复：传入 skill_repository
        )

        # 创建 Session Service
        self._session_service = InMemorySessionService()

        # 创建 Runner（严格照抄 quickstart 用法）
        self._runner = Runner(
            app_name="code_review_agent",
            agent=self._agent,
            session_service=self._session_service
        )

    async def review_code(self, diff_content: str, user_id: str = "user") -> str:
        """执行代码评审

        Args:
            diff_content: Git diff 内容
            user_id: 用户 ID，用于会话管理

        Returns:
            评审报告文本
        """
        # 创建新的会话（严格照抄 quickstart 用法）
        session_id = str(uuid.uuid4())

        # 创建会话状态变量
        await self._session_service.create_session(
            app_name="code_review_agent",
            user_id=user_id,
            session_id=session_id,
            state={
                "review_phase": "initial",
                "diff_content": diff_content
            }
        )

        # 构造用户消息（严格照抄 quickstart 用法）
        user_message = Content(
            parts=[Part.from_text(text=f"""请评审以下代码变更：

```
{diff_content}
```

请按以下步骤执行评审：
1. 使用 skill_load("code-review") 加载代码审查技能
2. 使用 skill_run 执行 static_review.py 进行静态分析
3. 使用 skill_run 执行 diff_summary.py 生成变更摘要
4. 综合分析结果，生成完整报告
""")]
        )

        # 执行 Runner（严格照抄 quickstart 用法）
        full_response = ""
        async for event in self._runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=user_message
        ):
            # 检查 event.content 是否存在
            if not event.content or not event.content.parts:
                continue

            # 处理流式输出
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        full_response += part.text
                        print(part.text, end="", flush=True)
                continue

            # 处理最终输出
            for part in event.content.parts:
                # 跳过推理部分（partial=True 时已输出）
                if part.thought:
                    continue
                # 工具调用
                if part.function_call:
                    print(f"\n🔧 [调用工具: {part.function_call.name}("
                          f"{part.function_call.args})]")
                # 工具结果
                elif part.function_response:
                    print(f"\n📊 [工具结果: {part.function_response.response}]")
                # 文本输出
                elif part.text:
                    full_response += part.text
                    print(part.text, end="", flush=True)

        print()  # 换行
        return full_response

    async def close(self):
        """关闭 Agent，释放资源"""
        # InMemorySessionService 无需显式关闭
        pass


async def main():
    """主函数示例"""
    import sys

    # 读取 diff 内容（从文件或 stdin）
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r', encoding='utf-8') as f:
            diff_content = f.read()
    else:
        diff_content = sys.stdin.read()

    if not diff_content.strip():
        print("错误：未提供 diff 内容", file=sys.stderr)
        sys.exit(1)

    # 创建 Code Review Agent
    agent = CodeReviewAgent()

    try:
        # 执行代码评审
        print("🔍 开始代码评审...")
        await agent.review_code(diff_content)
        print("\n✅ 评审完成")
        return 0
    except Exception as e:
        print(f"\n❌ 评审失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    finally:
        await agent.close()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
