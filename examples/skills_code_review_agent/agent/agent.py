#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""LlmAgent and SkillToolSet assembly for code review."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
import json
import logging
from pathlib import Path
import re
from typing import Any

from code_review.redaction import redact_text
from code_review.trace import TraceSink, emit_trace
from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, LlmResponse
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.skills import create_default_skill_repository
from trpc_agent_sdk.skills.tools import CopySkillStager
from trpc_agent_sdk.types import Content, Part

from agent.prompts import AGENT_INSTRUCTION
from agent.tools import (
    AgentWorkspaceBinder,
    CodeReviewSkillToolSet,
    ControlledSkillRunTool,
    PipelinePort,
    ReviewRequestRegistry,
)


class AgentExecutionError(RuntimeError):
    """表示 Agent 未完成规定的 Skill 工具链，错误文本仅使用固定代码。"""


PublicResponseObserver = Callable[[str], None]
_OPAQUE_REQUEST_ID_PATTERN = re.compile(r"review-request-[0-9a-f]{32}")
_LOGGER = logging.getLogger("code_review_agent")


class _AgentAssemblyModel(LLMModel):
    """为 fake/dry-run 生成确定性的 skill_load → skill_run 工具调用。"""

    def __init__(self, model_name: str) -> None:
        """初始化每次 review 都会重置的离线工具调用步数。"""

        super().__init__(model_name)
        self._step = 0

    def begin_review(self) -> None:
        """为新的评审请求重置固定三步状态机。"""

        self._step = 0

    @classmethod
    def supported_models(cls) -> list[str]:
        """声明内部固定模型名，避免注册或读取外部模型配置。"""

        return [r"code-review-agent-assembly"]

    async def _generate_async_impl(
        self,
        request: Any,
        stream: bool = False,
        ctx: Any = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        """根据既有工具响应推进固定两步流程，不解析或接收原始评审输入。"""

        del stream, ctx
        request_id = _review_request_id(request)
        if self._step == 0:
            self._step = 1
            yield LlmResponse(
                content=Content(
                    parts=[
                        Part.from_function_call(
                            name="skill_load",
                            args={"skill_name": "code-review", "docs": [], "include_all_docs": False},
                        )
                    ]
                )
            )
            return
        if self._step == 1:
            self._step = 2
            yield LlmResponse(
                content=Content(
                    parts=[
                        Part.from_function_call(
                            name="skill_run",
                            args={"review_request_id": request_id},
                        )
                    ]
                )
            )
            return
        yield LlmResponse(content=Content(parts=[Part.from_text(text="评审已完成，结构化报告已生成。")]))

    def validate_request(self, request: Any) -> None:
        """复用 SDK 基础输入验证，使组装模型的失败语义与普通模型一致。"""

        super().validate_request(request)


class CodeReviewAgent:
    """将 SDK Agent/Skill 能力与唯一 ReviewPipeline 组合的公开入口。"""

    def __init__(
        self,
        *,
        pipeline: PipelinePort,
        skill_root: Path,
        model: LLMModel | None = None,
        workspace_runtime: BaseWorkspaceRuntime | None = None,
        workspace_binder: AgentWorkspaceBinder | None = None,
        review_deadline_seconds: float = 110.0,
    ) -> None:
        """组装只暴露 skill_load/skill_run 的 SkillToolSet，并保留唯一 pipeline。"""

        if review_deadline_seconds <= 0:
            raise ValueError("agent_review_deadline_invalid")
        self._pipeline = pipeline
        self._review_deadline_seconds = float(review_deadline_seconds)
        self._requests = ReviewRequestRegistry()
        self.skill_repository = create_default_skill_repository(
            str(skill_root),
            workspace_runtime=workspace_runtime,
        )
        controlled_run_tool = ControlledSkillRunTool(
            pipeline=self._pipeline,
            requests=self._requests,
            workspace_binder=workspace_binder,
        )
        self.skill_toolset = CodeReviewSkillToolSet(
            repository=self.skill_repository,
            controlled_run_tool=controlled_run_tool,
            skill_stager=CopySkillStager(),
        )
        resolved_model = model or _AgentAssemblyModel("code-review-agent-assembly")
        self._model = resolved_model
        self.llm_agent = LlmAgent(
            name="code_review_agent",
            model=resolved_model,
            tools=[self.skill_toolset],
            instruction=AGENT_INSTRUCTION,
            skill_repository=self.skill_repository,
        )
        self.agent_run_count = 0
        self.last_tool_trace: tuple[str, ...] = ()
        self.last_prompt = ""

    def review(
        self,
        *,
        user_instruction: str | None = None,
        trace: TraceSink | None = None,
        public_response_observer: PublicResponseObserver | None = None,
        **input_options: Any,
    ) -> Any:
        """登记结构化输入并执行 Agent；可选观察器仅收到脱敏、截断后的公开最终文本。"""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise AgentExecutionError("agent_sync_api_requires_no_running_loop")
        request_id = self._requests.register(input_options, trace=trace)
        emit_trace(
            trace,
            "agent.turn_started",
            input_type=_trace_input_type(input_options),
        )
        if isinstance(self._model, _AgentAssemblyModel):
            self._model.begin_review()
        request_payload = {
            "operation": "controlled_code_review",
            "review_request_id": request_id,
            "input_kinds": sorted(name for name, value in input_options.items() if value is not None),
        }
        if user_instruction:
            request_payload["user_request"] = user_instruction
        prompt = json.dumps(
            request_payload,
            ensure_ascii=False,
            sort_keys=True,
        )
        self.last_prompt = prompt
        try:
            try:
                asyncio.run(
                    asyncio.wait_for(
                        self._run_sdk_agent(
                            prompt,
                            request_id,
                            trace,
                            public_response_observer,
                        ),
                        timeout=self._review_deadline_seconds,
                    )
                )
            except asyncio.TimeoutError as exc:
                raise AgentExecutionError("agent_review_deadline_exceeded") from exc
            result, error = self._requests.outcome(request_id)
            if error:
                raise AgentExecutionError(error)
            if self.last_tool_trace != ("skill_load", "skill_run"):
                raise AgentExecutionError("skill_tool_sequence_invalid")
            if result is None:
                raise AgentExecutionError("skill_run_not_completed")
            emit_trace(trace, "agent.turn_completed", status=_result_status(result))
            return result
        finally:
            self._requests.discard(request_id)

    async def _run_sdk_agent(
        self,
        prompt: str,
        request_id: str,
        trace: TraceSink | None,
        public_response_observer: PublicResponseObserver | None,
    ) -> None:
        """执行无原始代码的 Agent 回合，记录工具顺序并可选转交安全的公开最终文本。"""

        service = InMemorySessionService()
        runner = Runner(
            app_name="code_review_agent",
            agent=self.llm_agent,
            session_service=service,
            enable_post_turn_processing=False,
        )
        tool_trace: list[str] = []
        try:
            message = Content(parts=[Part.from_text(text=prompt)])
            async for event in runner.run_async(
                user_id="code_review",
                session_id=request_id,
                new_message=message,
            ):
                if event.content is None:
                    continue
                for part in event.content.parts:
                    if part.function_call:
                        tool_trace.append(part.function_call.name)
                        if len(tool_trace) > 2:
                            raise AgentExecutionError("agent_tool_call_limit_exceeded")
                        _LOGGER.info("Agent tool call: %s", part.function_call.name)
                        emit_trace(
                            trace,
                            "agent.tool_call",
                            tool=part.function_call.name,
                        )
                    elif part.function_response:
                        _LOGGER.info("Agent tool response: %s", _response_tool_name(part.function_response))
                        emit_trace(
                            trace,
                            "agent.tool_response",
                            tool=_response_tool_name(part.function_response),
                        )
                    elif part.text:
                        _notify_public_response_observer(public_response_observer, part.text)
            self.agent_run_count += 1
        finally:
            self.last_tool_trace = tuple(tool_trace)
            await runner.close()


def create_review_agent(
    *,
    pipeline: PipelinePort,
    skill_root: Path,
    model: LLMModel | None = None,
    workspace_runtime: BaseWorkspaceRuntime | None = None,
    workspace_binder: AgentWorkspaceBinder | None = None,
    review_deadline_seconds: float = 110.0,
) -> CodeReviewAgent:
    """构造真实调用受控 Skill 工具链的代码评审 Agent。"""

    return CodeReviewAgent(
        pipeline=pipeline,
        skill_root=skill_root,
        model=model,
        workspace_runtime=workspace_runtime,
        workspace_binder=workspace_binder,
        review_deadline_seconds=review_deadline_seconds,
    )


def _review_request_id(request: Any) -> str:
    """从宿主安全 JSON 中读取 request id，忽略其他模型上下文。"""

    for content in request.contents:
        for part in content.parts:
            if not part.text:
                continue
            try:
                payload = json.loads(part.text)
            except (TypeError, ValueError):
                continue
            request_id = payload.get("review_request_id") if isinstance(payload, dict) else None
            if isinstance(request_id, str) and request_id:
                return request_id
    return "review-request-invalid"


def _trace_input_type(input_options: dict[str, Any]) -> str:
    """将受控输入键映射为安全枚举，禁止 trace 读取路径或载荷。"""

    for name in ("fixture", "diff_file", "repo_path", "files"):
        if input_options.get(name) is not None:
            return name.removesuffix("_file").removesuffix("_path")
    return "unknown"


def _response_tool_name(response: Any) -> str:
    """仅从 SDK 工具响应提取声明名称，缺失时使用固定占位符。"""

    name = getattr(response, "name", "tool")
    return name if isinstance(name, str) else "tool"


def _result_status(result: Any) -> str:
    """兼容真实 PipelineResult 与测试替身的状态字段，避免 trace 改变既有端口。"""

    if isinstance(result, dict):
        status = result.get("status", "completed")
    else:
        status = getattr(result, "status", "completed")
    return status if isinstance(status, str) else "completed"


def _notify_public_response_observer(
    observer: PublicResponseObserver | None,
    text: str,
) -> None:
    """向显式诊断观察器转交模型公开文本，并移除凭据、一次性标识及超长内容。"""

    if observer is None:
        return
    safe_text = _OPAQUE_REQUEST_ID_PATTERN.sub("[REDACTED:request_id]", redact_text(text)).strip()
    if not safe_text:
        return
    try:
        observer(safe_text[:1000])
    except Exception:
        return


__all__ = ["AgentExecutionError", "CodeReviewAgent", "create_review_agent"]
