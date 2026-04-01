# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Agent Session Summarizer Filter

本模块实现了基于 Agent Filter 的会话总结功能。

与 SessionService 层面的总结不同，Agent 层面的总结具有以下特点：
1. 细粒度控制：每个 Agent 独立总结自己产生的事件
2. 多 Agent 支持：适合多 Agent 协作场景，避免总结冲突
3. 灵活触发：可以基于对话文本长度、Agent 执行完成等条件触发

使用方式：
    在 agent/agent.py 中启用 Filter：

    ```python
    def create_agent() -> LlmAgent:
        agent = LlmAgent(
            name="python_tutor",
            model=_create_model(),
            instruction=INSTRUCTION,
            filters=[AgentSessionSummarizerFilter(_create_model())],
        )
        return agent
    ```

工作流程：
    1. _after_every_stream: 每次流式返回后收集事件
    2. 检查对话文本长度是否超过阈值（12KB）
    3. _after: Agent 执行完成后执行总结
    4. _do_summarize: 从 Session 中移除 Agent 事件，进行总结，替换为压缩后的事件
"""

from typing import Any

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import get_invocation_ctx
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.sessions import SessionSummarizer


# 每个 agent 可以绑定多个 filter
# filter 采用洋葱模型执行
# @register_agent_filter("agent_session_summarizer_filter")  # 注册到框架中，到时在 agent 中绑定名字即可使用
class AgentSessionSummarizerFilter(BaseFilter):
    """Agent session summarizer filter.

    基于 Agent Filter 的会话总结器，用于在 Agent 层面压缩对话历史。

    与 SessionService 层面的总结相比，这种方式更适合：
    - 多 Agent 协作场景：每个 Agent 独立总结自己的对话
    - 细粒度控制：可以针对不同 Agent 配置不同的总结策略
    - 避免冲突：Agent 层面的总结不会与 SessionService 层面的总结冲突

    触发条件：
    - 每次流式返回后检查对话文本长度（默认阈值：12KB）
    - Agent 执行完成后强制执行一次总结

    示例：
        ```python
        # 在 agent/agent.py 中使用
        agent = LlmAgent(
            name="python_tutor",
            model=model,
            filters=[AgentSessionSummarizerFilter(model)],
        )
        ```
    """

    def __init__(self, model: OpenAIModel):
        """初始化 Agent Session Summarizer Filter.

        Args:
            model: 用于总结的 LLM 模型实例
        """
        super().__init__()
        # 创建总结器
        # 注意：这里不设置 check_summarizer_functions，因为触发逻辑在 Filter 中控制
        self.summarizer = SessionSummarizer(
            model=model,
            max_summary_length=600,  # 保留的总结文本长度, 默认是 1000， 超过该长度显示 ...
        )

    async def _after_every_stream(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """每次流式返回后的处理逻辑.

        在 Agent 流式返回每个事件时调用，用于：
        1. 收集 Agent 产生的所有事件到 ctx.metadata["events"]
        2. 检查对话文本长度，超过阈值时触发总结

        Args:
            ctx: Agent 上下文，包含 metadata 等信息
            req: 请求对象
            rsp: FilterResult，其中 rsp.rsp 是 Event 类型
        """
        # 当前 agent 流式每次返回一个 event, rsp 是 FilterResult 类型, 这里的 rsp.rsp 是 Event 类型
        # check if need to summarize
        if not rsp.rsp.partial:
            # 获取已收集的事件
            events = ctx.metadata.get("events", [])
            # 提取对话文本
            conversation_text = self.summarizer._extract_conversation_text(events)
            # 如果对话文本超过 12KB，触发总结
            # 注意：这个阈值可以根据实际需求调整
            if len(conversation_text) > 12 * 1024:
                await self._do_summarize(ctx)

        # 将执行的 event 放在上下文中缓存
        # 这些事件会在 _do_summarize 中被处理
        if "events" not in ctx.metadata:
            ctx.metadata["events"] = []
        ctx.metadata["events"].append(rsp.rsp)

    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Agent 执行完成后的处理逻辑.

        在 Agent 执行完成后调用，确保所有事件都被处理。
        无论是否在流式过程中触发过总结，这里都会执行一次总结，
        确保 Agent 产生的所有事件都被压缩。

        Args:
            ctx: Agent 上下文
            req: 请求对象
            rsp: FilterResult
        """
        # 最后执行一次总结，确保所有事件都被处理
        await self._do_summarize(ctx)

    async def _do_summarize(self, ctx: AgentContext):
        """执行总结操作的核心逻辑.

        主要步骤：
        1. 从全局 Session 中移除 Agent 的事件（避免重复总结）
        2. 提取对话文本
        3. 调用 create_session_summary_by_events 进行总结
        4. 将压缩后的事件添加回 Session

        注意：
        - 如果是多个 agent 并发执行，这里需要加协程锁保证顺序
        - 异步网络操作可能会切出协程，导致顺序错乱

        Args:
            ctx: Agent 上下文，包含 metadata 和事件信息
        """
        # 获取当前调用的上下文
        invocation_ctx: InvocationContext = get_invocation_ctx()

        # 获取该 agent 执行产生的 event
        # 使用 pop 确保事件只被处理一次
        events = ctx.metadata.pop("events", [])

        # 如果是多个 agent 并发执行，这里需要加协程锁保证顺序
        # 异步网络操作可能会切出协程，导致顺序错乱
        # 示例：使用 asyncio.Lock()
        # async with self._lock:
        #     ... 总结逻辑 ...

        print(
            f"\n\n {invocation_ctx.agent.name} agent: before summary agent events length: {len(invocation_ctx.session.events)}\n\n"
        )

        # 在全局 session 中删除该 agent 保留的 events
        # 这一步很重要：避免 SessionService 层面的总结重复处理这些事件
        for event in events:
            if event in invocation_ctx.session.events:
                invocation_ctx.session.events.remove(event)

        print(
            f"\n\n {invocation_ctx.agent.name}: after summary agent events length: {len(invocation_ctx.session.events)}\n\n"
        )

        session_id = invocation_ctx.session.id

        # 提取对话文本（用于调试和日志）
        conversation_text = self.summarizer._extract_conversation_text(events)
        print(
            f"\n\n {invocation_ctx.agent.name} agent: conversation_text: {conversation_text}\n--------------------------------\n"
        )

        # 对这些 agent 产生的事件做总结
        # create_session_summary_by_events 是专门用于 Agent 层面总结的方法
        # 它接受事件列表和 session_id，返回总结文本和压缩后的事件
        summary_text, compressed_events = await self.summarizer.create_session_summary_by_events(events,
                                                                                                 session_id,
                                                                                                 ctx=invocation_ctx)

        # 将压缩后的事件添加回 session
        # 压缩后的事件数量通常远少于原始事件，从而减少 token 消耗
        if compressed_events:
            invocation_ctx.session.events.extend(compressed_events)

        print(
            f"\n\n {invocation_ctx.agent.name} agent: after {len(invocation_ctx.session.events)} summary_text: {summary_text}\n--------------------------------\n"
        )
