#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""受控 Agent Skill 工具边界的单元测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.tools import ControlledSkillRunTool, ReviewRequestRegistry
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.skills import loaded_state_key


class _StubAgent(BaseAgent):
    """提供构造 InvocationContext 所需的最小 Agent。"""

    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """保持空事件流，因为工具单测不会执行 Agent 对话。"""

        del ctx
        if False:
            yield


class _RecordingPipeline:
    """记录公开 run 调用，并返回固定的脱敏报告摘要。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        """保存可选异常和调用记录，供副作用断言使用。"""

        self.error = error
        self.calls: list[dict[str, Any]] = []

    def run(self, **options: Any) -> dict[str, Any]:
        """记录调用；配置异常时抛出，否则返回固定 canonical 摘要。"""

        self.calls.append(options)
        if self.error is not None:
            raise self.error
        return {
            "status": "completed",
            "task_id": "review-safe-task",
            "findings": [{"category": "security"}],
            "needs_human_review": [{"category": "tests"}],
            "warnings": [{"code": "local_runtime"}],
        }


class _RecordingBinder:
    """记录 Agent workspace 绑定上下文的进入和退出。"""

    def __init__(self) -> None:
        """初始化绑定生命周期计数。"""

        self.enter_count = 0
        self.exit_count = 0

    @contextmanager
    def bind_agent_workspace(
        self,
        workspace_id: str,
        context: InvocationContext,
    ) -> Iterator[None]:
        """在受控调用期间记录 workspace 上下文生命周期。"""

        assert workspace_id == context.session_id
        self.enter_count += 1
        try:
            yield
        finally:
            self.exit_count += 1


def _invocation_context(*, skill_loaded: bool = True) -> InvocationContext:
    """构造隔离的 SDK 调用上下文，并按需标记 code-review Skill 已加载。"""

    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(
            app_name="agent-tools-test",
            user_id="user-1",
            session_id="session-1",
        )
    )
    agent = _StubAgent(name="code_review_agent")
    context = InvocationContext(
        session_service=service,
        invocation_id="invocation-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
    )
    if skill_loaded:
        context.actions.state_delta[loaded_state_key(context, "code-review")] = True
    return context


def test_controlled_skill_run_consumes_each_request_only_once() -> None:
    """验证同一 request id 重放时不会再次调用 Pipeline。"""

    pipeline = _RecordingPipeline()
    registry = ReviewRequestRegistry()
    request_id = registry.register({"fixture": "01_clean_simple"})
    tool = ControlledSkillRunTool(pipeline=pipeline, requests=registry)
    context = _invocation_context()

    first = asyncio.run(
        tool.run_async(
            tool_context=context,
            args={"review_request_id": request_id},
        )
    )
    replay = asyncio.run(
        tool.run_async(
            tool_context=context,
            args={"review_request_id": request_id},
        )
    )

    assert first == {
        "status": "completed",
        "task_id": "review-safe-task",
        "finding_count": 1,
        "needs_human_review_count": 1,
        "warning_count": 1,
    }
    assert replay == {
        "status": "blocked",
        "error": "review_request_invalid_or_reused",
    }
    assert len(pipeline.calls) == 1
    assert pipeline.calls[0]["entrypoint_tool_call_count"] == 2


def test_controlled_skill_run_sanitizes_pipeline_failures() -> None:
    """验证 Pipeline 异常只返回固定错误码，不泄露异常正文。"""

    secret_text = "synthetic-password-must-not-leak"
    pipeline = _RecordingPipeline(error=RuntimeError(secret_text))
    registry = ReviewRequestRegistry()
    request_id = registry.register({"fixture": "02_security_simple"})
    tool = ControlledSkillRunTool(pipeline=pipeline, requests=registry)

    result = asyncio.run(
        tool.run_async(
            tool_context=_invocation_context(),
            args={"review_request_id": request_id},
        )
    )
    outcome, error = registry.outcome(request_id)

    assert result == {
        "status": "failed",
        "error": "review_pipeline_failed",
    }
    assert outcome is None
    assert error == "review_pipeline_failed"
    assert secret_text not in repr(result)
    assert secret_text not in error


def test_controlled_skill_run_scopes_workspace_binding_to_one_call() -> None:
    """验证 workspace binder 在 Pipeline 调用前进入，并在结束后退出。"""

    pipeline = _RecordingPipeline()
    binder = _RecordingBinder()
    registry = ReviewRequestRegistry()
    request_id = registry.register({"fixture": "01_clean_simple"})
    tool = ControlledSkillRunTool(
        pipeline=pipeline,
        requests=registry,
        workspace_binder=binder,
    )

    result = asyncio.run(
        tool.run_async(
            tool_context=_invocation_context(),
            args={"review_request_id": request_id},
        )
    )

    assert result["status"] == "completed"
    assert binder.enter_count == 1
    assert binder.exit_count == 1
    assert len(pipeline.calls) == 1
