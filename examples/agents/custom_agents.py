#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Custom Agents 示例 - 智能文档处理工作流

演示如何创建Custom Agent实现复杂的条件逻辑：
- 根据文档类型动态选择处理策略
- 基于处理结果决定是否需要额外验证
- 展示状态管理和控制流程
"""

import asyncio
import os
import uuid
from typing import AsyncGenerator

from pydantic import ConfigDict
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.agents import ChainAgent
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.events import create_text_event
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


class SmartDocumentProcessor(BaseAgent):
    """智能文档处理Custom Agent

    根据文档类型和复杂度动态选择处理策略：
    - 简单文档：直接处理
    - 复杂文档：分析→处理→验证
    - 技术文档：特殊处理流程

    这展示了Custom Agents的核心能力：条件逻辑和动态Agent选择
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, )

    document_analyzer: LlmAgent
    simple_processor: LlmAgent
    complex_processor_chain: ChainAgent  # 包装complex_analyzer和complex_processor
    technical_processor: LlmAgent
    quality_validator: LlmAgent

    def __init__(self, model, **kwargs):
        # 第一步：文档类型分析Agent
        document_analyzer = LlmAgent(
            name="document_analyzer",
            model=model,
            description="Analyze document type and complexity",
            instruction="""分析输入文档的类型和复杂度。

根据以下标准进行分类：
- simple: 简单的信息查询、基础说明文档
- complex: 需要深度分析的报告、多步骤处理的内容
- technical: 技术文档、代码相关、需要专业知识的内容

只输出分类结果：simple、complex 或 technical 中的一个。""",
            output_key="doc_type",
        )

        # 简单文档处理Agent
        simple_processor = LlmAgent(
            name="simple_processor",
            model=model,
            description="Process simple documents efficiently",
            instruction="""你是一个高效的文档处理助手，专门处理简单文档。

请处理以下文档内容：{user_input}

要求：
- 提供清晰、准确的处理结果
- 保持简洁明了的风格
- 确保信息的完整性""",
            output_key="processed_content",
        )

        # 复杂文档处理链：分析→处理
        complex_analyzer = LlmAgent(
            name="complex_analyzer",
            model=model,
            description="Analyze complex document structure",
            instruction="""你是一个专业的文档分析师，专门分析复杂文档。

请深度分析以下文档：{user_input}

分析内容包括：
1. 文档结构和组织
2. 关键信息和要点
3. 逻辑关系和层次
4. 潜在的处理难点

输出结构化的分析结果。""",
            output_key="complex_analysis",
        )

        complex_processor = LlmAgent(
            name="complex_processor",
            model=model,
            description="Process complex documents based on analysis",
            instruction="""基于详细分析处理复杂文档。

分析结果：{complex_analysis}

原始文档：{user_input}

请基于分析结果：
1. 提取核心信息
2. 重组文档结构
3. 补充必要说明
4. 确保逻辑清晰

输出完整的处理结果。""",
            output_key="processed_content",
        )

        # 使用ChainAgent封装复杂文档处理流程
        complex_processor_chain = ChainAgent(
            name="complex_processor_chain",
            description="Complex document processing: analyze → process",
            sub_agents=[complex_analyzer, complex_processor],
        )

        # 技术文档处理Agent
        technical_processor = LlmAgent(
            name="technical_processor",
            model=model,
            description="Process technical documents with specialized approach",
            instruction="""你是一个技术文档专家，专门处理技术相关文档。

技术文档内容：{user_input}

处理要求：
1. 保持技术术语的准确性
2. 维护代码和配置的正确性
3. 提供清晰的技术说明
4. 确保可操作性

输出专业的技术文档处理结果。""",
            output_key="processed_content",
        )

        # 质量验证Agent
        quality_validator = LlmAgent(
            name="quality_validator",
            model=model,
            description="Validate processing quality and suggest improvements",
            instruction="""验证文档处理质量并提供改进建议。

处理后的内容：{processed_content}

验证标准：
1. 信息准确性 (1-10分)
2. 结构清晰度 (1-10分)
3. 完整性 (1-10分)
4. 可读性 (1-10分)

如果所有分数都在8分以上，输出"质量验证通过"。
如果任何分数低于8分，提供具体的改进建议。""",
            output_key="quality_feedback",
        )

        # 将所有Agent添加到sub_agents
        sub_agents_list = [
            document_analyzer,
            simple_processor,
            complex_processor_chain,  # 使用ChainAgent封装的复杂文档处理流程
            technical_processor,
            quality_validator,
        ]

        # 调用父类构造函数，传递所有agents作为关键字参数
        super().__init__(
            document_analyzer=document_analyzer,
            simple_processor=simple_processor,
            complex_processor_chain=complex_processor_chain,
            technical_processor=technical_processor,
            quality_validator=quality_validator,
            sub_agents=sub_agents_list,
            **kwargs,
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """实现智能文档处理的自定义编排逻辑

        这是Custom Agent的核心方法，展示了：
        1. 条件逻辑：根据文档类型选择不同处理流程
        2. 状态管理：在Agent间传递分析结果
        3. 动态决策：基于处理结果决定是否需要验证
        """
        print(f"  📋 [{self.name}] 开始智能文档处理工作流")

        # 发送内部日志- 记录工作流启动
        internal_start = create_text_event(
            ctx=ctx,
            text=f"[内部日志] 工作流启动，session_id={ctx.session.id}",
        )
        yield internal_start

        # 第一阶段：文档类型分析
        print(f"  🔍 [{self.name}] 阶段1: 分析文档类型...")
        async for event in self.document_analyzer.run_async(ctx):
            yield event

        # 获取分析结果并做决策
        doc_type = ctx.session.state.get("doc_type", "simple").lower().strip()
        print(f"  📊 [{self.name}] 文档类型识别: {doc_type}")

        # 第二阶段：根据文档类型选择处理策略
        if doc_type == "simple":
            print(f"  ⚡ [{self.name}] 阶段2: 使用简单处理流程...")
            async for event in self.simple_processor.run_async(ctx):
                yield event

        elif doc_type == "complex":
            print(f"  🧠 [{self.name}] 阶段2: 使用复杂文档处理流程...")
            print(f"  🔗 [{self.name}] 使用ChainAgent: 分析→处理")

            # 使用ChainAgent自动执行 分析→处理 的流程
            async for event in self.complex_processor_chain.run_async(ctx):
                yield event

        elif doc_type == "technical":
            print(f"  🔧 [{self.name}] 阶段2: 使用技术文档专用流程...")
            async for event in self.technical_processor.run_async(ctx):
                yield event

            # 技术文档也需要验证
            async for event in self.quality_validator.run_async(ctx):
                yield event
        else:
            print(f"  ❓ [{self.name}] 未知文档类型，使用简单处理...")
            async for event in self.simple_processor.run_async(ctx):
                yield event

        # 第三阶段：质量验证决策
        # 只有复杂文档和技术文档需要质量验证
        if doc_type in ["complex", "technical"]:
            print(f"  ✅ [{self.name}] 阶段3: 执行质量验证...")
            async for event in self.quality_validator.run_async(ctx):
                yield event

            # 检查验证结果，可以基于此做进一步决策
            quality_feedback = ctx.session.state.get("quality_feedback", "")
            if "质量验证通过" in quality_feedback:
                print(f"  🎉 [{self.name}] 质量验证通过，处理完成!")
            else:
                print(f"  📝 [{self.name}] 质量验证发现改进点，已提供建议")
        else:
            print(f"  ⏭️  [{self.name}] 简单文档跳过质量验证阶段")

        # 发送完成日志- 记录工作流完成
        internal_complete = create_text_event(
            ctx=ctx,
            text=f"[内部日志] 工作流完成，文档类型={doc_type}",
        )
        yield internal_complete

        print(f"  ✨ [{self.name}] 智能文档处理工作流完成!")


def create_agent():
    """创建智能文档处理的Custom Agent"""

    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )

    return SmartDocumentProcessor(
        name="smart_document_processor",
        description="智能文档处理系统，根据文档类型动态选择最优处理策略",
        model=model,
    )


async def run_agent():
    """运行Custom Agent演示"""

    # 定义应用和用户信息
    APP_NAME = "custom_agent_demo"
    USER_ID = "demo_user"

    print("=" * 60)
    print("Custom Agents 演示 - 智能文档处理工作流")
    print("=" * 60)
    print("展示特性：")
    print("• 条件逻辑 - 根据文档类型选择处理策略")
    print("• 状态管理 - 在Agent间传递分析结果")
    print("• 动态决策 - 基于处理结果决定验证需求")
    print("=" * 60)

    # 创建Custom Agent
    custom_agent = create_agent()

    # 创建Runner
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=custom_agent, session_service=session_service)

    # 测试不同类型的文档
    test_documents = [
        {
            "title": "简单文档示例",
            "content": "请解释什么是人工智能，以及它在日常生活中的应用。",
            "expected_type": "simple",
        },
        {
            "title": "复杂文档示例",
            "content": """公司年度财务报告摘要：

营收增长分析：
本年度总营收达到500万元，相比去年增长25%。主要增长来源包括：
1. 核心产品销售增长30%
2. 新产品线贡献15%的收入
3. 海外市场扩张带来20%增量

成本结构优化：
通过供应链重组和自动化改进，运营成本下降了8%。

市场前景：
基于当前趋势分析，预计明年增长率将保持在20-30%区间。

需要深入分析各项数据的关联性和趋势。""",
            "expected_type": "complex",
        },
        {
            "title": "技术文档示例",
            "content": """Python异步编程最佳实践：

1. 使用async/await语法
async def fetch_data(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.json()

2. 避免在异步函数中使用阻塞调用
# 错误示例
async def bad_example():
    time.sleep(1)  # 阻塞调用

# 正确示例
async def good_example():
    await asyncio.sleep(1)  # 非阻塞

3. 使用asyncio.gather进行并发处理
results = await asyncio.gather(
    fetch_data(url1),
    fetch_data(url2),
    fetch_data(url3)
)

需要提供技术准确的说明和代码示例。""",
            "expected_type": "technical",
        },
    ]

    for i, doc in enumerate(test_documents, 1):
        print(f"\n{'='*20} 测试案例 {i}: {doc['title']} {'='*20}")
        print(f"预期类型: {doc['expected_type']}")
        print(f"文档内容: {doc['content'][:100]}...")
        print("\n处理过程:")

        session_id = str(uuid.uuid4())

        # 创建session并将user_input放入state中
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
            state={"user_input": doc["content"]},  # 将用户输入内容放入session state
        )

        user_message = Content(parts=[Part.from_text(text=doc["content"])])

        async for event in runner.run_async(user_id=USER_ID, session_id=session_id, new_message=user_message):
            if event.content and event.content.parts and event.author != "user":
                if not event.partial:
                    for part in event.content.parts:
                        if part.text:
                            # 只显示最终结果，中间过程通过print已经显示
                            if event.author == "smart_document_processor":
                                print("\n📄 最终处理结果:")
                                print(f"   {part.text}")
                            # 可以取消注释查看各个子Agent的详细输出
                            # else:
                            #     print(f"[{event.author}] {part.text[:200]}...")

        print("-" * 80)


if __name__ == "__main__":
    print("🎯 Custom Agents 示例")
    print("展示如何实现条件逻辑和动态Agent选择")

    asyncio.run(run_agent())
