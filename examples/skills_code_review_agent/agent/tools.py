#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""受控 code-review Skill 工具和一次性评审请求注册表。"""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from dataclasses import dataclass
import threading
import uuid
from typing import Any, Protocol

from code_review.trace import TraceSink, emit_trace
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.skills import SkillToolSet, loaded_state_key
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration, Schema, Type


_SKILL_NAME = "code-review"


class PipelinePort(Protocol):
    """定义受控 skill_run 唯一允许委托的 ReviewPipeline 接口。"""

    def run(self, **input_options: Any) -> Any:
        """执行唯一评审链路并返回 canonical pipeline 结果。"""


class AgentWorkspaceBinder(Protocol):
    """定义 Agent skill_load workspace 绑定到 pipeline sandbox 的安全接口。"""

    def bind_agent_workspace(
        self,
        workspace_id: str,
        context: InvocationContext,
    ) -> AbstractContextManager[None]:
        """在当前 skill_run 生命周期内复用已加载 Skill 的隔离 workspace。"""


@dataclass
class _PendingReview:
    """保存一次只能消费一次的结构化评审输入及其执行结果。"""

    input_options: dict[str, Any]
    result: Any = None
    error: str = ""
    consumed: bool = False
    trace: TraceSink | None = None


class ReviewRequestRegistry:
    """用不可预测标识隔离模型与原始评审输入，并记录工具执行结果。"""

    def __init__(self) -> None:
        """初始化进程内请求表和互斥锁。"""

        self._requests: dict[str, _PendingReview] = {}
        self._lock = threading.Lock()

    def register(
        self,
        input_options: dict[str, Any],
        *,
        trace: TraceSink | None = None,
    ) -> str:
        """登记结构化输入并返回不含输入内容的一次性 request id。"""

        request_id = f"review-request-{uuid.uuid4().hex}"
        with self._lock:
            self._requests[request_id] = _PendingReview(
                input_options=dict(input_options),
                trace=trace,
            )
        return request_id

    def consume(self, request_id: str) -> dict[str, Any] | None:
        """原子消费请求；未知或重复 id 返回空值且不产生 pipeline 副作用。"""

        with self._lock:
            pending = self._requests.get(request_id)
            if pending is None or pending.consumed:
                return None
            pending.consumed = True
            return dict(pending.input_options)

    def complete(self, request_id: str, result: Any) -> None:
        """保存 pipeline 结果，供 Agent 回合结束后返回给调用方。"""

        with self._lock:
            pending = self._requests.get(request_id)
            if pending is not None:
                pending.result = result

    def fail(self, request_id: str, error: str) -> None:
        """记录脱敏错误代码，禁止在工具响应中携带原始异常文本。"""

        with self._lock:
            pending = self._requests.get(request_id)
            if pending is not None:
                pending.error = error

    def outcome(self, request_id: str) -> tuple[Any, str]:
        """返回结果和脱敏错误代码；请求不存在时统一视为无效。"""

        with self._lock:
            pending = self._requests.get(request_id)
            if pending is None:
                return None, "review_request_invalid"
            return pending.result, pending.error

    def trace_for(self, request_id: str) -> TraceSink | None:
        """返回本次请求的短生命周期 trace sink，不暴露原始输入。"""

        with self._lock:
            pending = self._requests.get(request_id)
            return None if pending is None else pending.trace

    def discard(self, request_id: str) -> None:
        """删除本次短生命周期请求，避免结构化输入在 Agent 回合后滞留。"""

        with self._lock:
            self._requests.pop(request_id, None)


class ControlledSkillRunTool(BaseTool):
    """只接受 request id 的 skill_run，实际执行计划由宿主 pipeline 和 manifest 决定。"""

    def __init__(
        self,
        *,
        pipeline: PipelinePort,
        requests: ReviewRequestRegistry,
        workspace_binder: AgentWorkspaceBinder | None = None,
    ) -> None:
        """绑定唯一 pipeline 与请求注册表，不接受模型提供 command、路径或环境。"""

        super().__init__(
            name="skill_run",
            description=(
                "Run the already loaded code-review Skill for one host-approved review request. "
                "Only review_request_id is accepted; commands, paths, environment and code are host controlled."
            ),
        )
        self._pipeline = pipeline
        self._requests = requests
        self._workspace_binder = workspace_binder

    def _get_declaration(self) -> FunctionDeclaration:
        """仅向模型声明一次性 request id，避免暴露通用 skill_run 命令面。"""

        return FunctionDeclaration(
            name="skill_run",
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                required=["review_request_id"],
                properties={
                    "review_request_id": Schema(
                        type=Type.STRING,
                        description="Opaque one-time review request id supplied by the host.",
                    )
                },
            ),
            response=Schema(
                type=Type.OBJECT,
                description="Sanitized task status and finding bucket counts.",
            ),
        )

    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """验证 Skill 已加载和 request id 后，在线程中委托同步 ReviewPipeline。"""

        request_id = args.get("review_request_id")
        if not isinstance(request_id, str) or not request_id:
            return {"status": "blocked", "error": "review_request_invalid"}
        if not _skill_is_loaded(tool_context):
            self._requests.fail(request_id, "skill_load_required")
            return {"status": "blocked", "error": "skill_load_required"}
        input_options = self._requests.consume(request_id)
        if input_options is None:
            self._requests.fail(request_id, "review_request_invalid_or_reused")
            return {"status": "blocked", "error": "review_request_invalid_or_reused"}
        trace = self._requests.trace_for(request_id)
        emit_trace(trace, "skill_run.started", tool="skill_run")
        try:
            pipeline_options = {
                **input_options,
                "entrypoint_tool_call_count": 2,
            }
            if trace is not None:
                pipeline_options["trace"] = trace
            if self._workspace_binder is None:
                result = await asyncio.to_thread(self._pipeline.run, **pipeline_options)
            else:
                with self._workspace_binder.bind_agent_workspace(
                    tool_context.session_id,
                    tool_context,
                ):
                    result = await asyncio.to_thread(self._pipeline.run, **pipeline_options)
        except Exception:
            self._requests.fail(request_id, "review_pipeline_failed")
            return {"status": "failed", "error": "review_pipeline_failed"}
        self._requests.complete(request_id, result)
        emit_trace(trace, "skill_run.completed", status=_safe_trace_status(result))
        return _safe_result_summary(result)


class CodeReviewSkillToolSet(SkillToolSet):
    """将 SDK SkillToolSet 收窄为 skill_load 和受控 skill_run 两个工具。"""

    def __init__(
        self,
        *,
        controlled_run_tool: ControlledSkillRunTool,
        **kwargs: Any,
    ) -> None:
        """初始化 SDK ToolSet，并保存替换通用命令面的受控运行工具。"""

        super().__init__(**kwargs)
        self._controlled_run_tool = controlled_run_tool

    async def get_tools(
        self,
        invocation_context: InvocationContext | None = None,
    ) -> list[BaseTool]:
        """只暴露 SDK skill_load 与本项目受控 skill_run，隐藏其他 workspace 工具。"""

        tools = await super().get_tools(invocation_context)
        selected: list[BaseTool] = []
        for tool in tools:
            if tool.name == "skill_load":
                selected.append(tool)
            elif tool.name == "skill_run":
                selected.append(self._controlled_run_tool)
        return selected


def _skill_is_loaded(context: InvocationContext) -> bool:
    """检查当前 Agent 会话的 code-review Skill 加载状态。"""

    key = loaded_state_key(context, _SKILL_NAME)
    if context.actions.state_delta.get(key):
        return True
    return bool(context.session_state.get(key))


def _safe_result_summary(result: Any) -> dict[str, Any]:
    """仅返回状态和桶计数，禁止把 evidence、路径或原始输入反馈给模型。"""

    report = getattr(result, "report", result)
    if not isinstance(report, dict):
        report = {}
    status = getattr(result, "status", report.get("status", "completed"))
    task_id = getattr(result, "task_id", report.get("task_id", ""))
    return {
        "status": status if isinstance(status, str) else "completed",
        "task_id": task_id if isinstance(task_id, str) else "",
        "finding_count": len(report.get("findings", ())),
        "needs_human_review_count": len(report.get("needs_human_review", ())),
        "warning_count": len(report.get("warnings", ())),
    }


def _safe_trace_status(result: Any) -> str:
    """从 pipeline 结果提取固定状态码，避免 trace 读取报告正文。"""

    status = getattr(result, "status", "completed")
    return status if isinstance(status, str) else "completed"


__all__ = [
    "CodeReviewSkillToolSet",
    "ControlledSkillRunTool",
    "AgentWorkspaceBinder",
    "PipelinePort",
    "ReviewRequestRegistry",
]
