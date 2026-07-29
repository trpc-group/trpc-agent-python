#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Integration tests for the SDK-backed code-review Agent entry."""

from __future__ import annotations

from collections.abc import AsyncGenerator
import json
import sys
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.agent import AgentExecutionError, create_review_agent  # noqa: E402
from code_review.inputs import FixturePayload  # noqa: E402
from code_review.pipeline import ReviewPipeline  # noqa: E402
from code_review.store import SqlReviewStore  # noqa: E402
from trpc_agent_sdk.models import LLMModel, LlmResponse  # noqa: E402
from trpc_agent_sdk.types import Content, Part  # noqa: E402


class _Pipeline:
    """记录输入的最小 pipeline 替身，避免本测试复制实际检测规则。"""

    def __init__(self) -> None:
        """初始化调用记录。"""

        self.calls: list[dict[str, Any]] = []

    def run(self, **options: Any) -> dict[str, Any]:
        """返回固定 canonical finding 集合，验证 Agent 只委托唯一 pipeline。"""

        self.calls.append(options)
        return {"findings": [{"file": "src/a.py", "line": 1, "category": "security"}]}


class _DenyGovernance:
    """返回前置拒绝决定，用于证明 Agent 入口不能绕过 ReviewPipeline 的治理门。"""

    def decide(self, **_arguments: Any) -> dict[str, Any]:
        """返回不携带输入内容的固定拒绝事件。"""

        return {
            "action": "deny",
            "events": [
                {
                    "stage": "pre_execution",
                    "target": "run_checks",
                    "action": "deny",
                    "rule": "sandbox_governance",
                    "reasons": ["network_proof_missing"],
                }
            ],
            "warnings": [],
        }


class _NoRunSandbox:
    """记录沙箱副作用次数，并在治理正确时保持 execute 调用数为零。"""

    runtime_type = "fake"

    def __init__(self) -> None:
        """初始化执行和清理计数。"""

        self.execute_calls = 0
        self.cleanup_calls = 0

    def execute(self, **_arguments: Any) -> dict[str, Any]:
        """记录意外执行；正确的 deny 路径不应调用本方法。"""

        self.execute_calls += 1
        return {}

    def cleanup(self, **_arguments: Any) -> None:
        """记录 pipeline 的 finally 清理调用，清理本身不产生外部副作用。"""

        self.cleanup_calls += 1


def _request_id(request: Any) -> str:
    """从测试模型收到的安全 JSON 中提取一次性评审请求标识。"""

    for content in request.contents:
        for part in content.parts:
            if not part.text:
                continue
            try:
                payload = json.loads(part.text)
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("review_request_id"), str):
                return payload["review_request_id"]
    return "missing-request-id"


class _SkipLoadModel(LLMModel):
    """模拟恶意模型跳过 skill_load 并直接请求 skill_run。"""

    @classmethod
    def supported_models(cls) -> list[str]:
        """声明测试专用模型名。"""

        return [r"skip-load"]

    async def _generate_async_impl(
        self,
        request: Any,
        stream: bool = False,
        ctx: Any = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        """首次直接调用 skill_run，收到工具响应后结束。"""

        del stream, ctx
        has_response = any(
            part.function_response
            for content in request.contents
            for part in content.parts
        )
        if has_response:
            yield LlmResponse(content=Content(parts=[Part.from_text(text="done")]))
            return
        yield LlmResponse(
            content=Content(
                parts=[
                    Part.from_function_call(
                        name="skill_run",
                        args={"review_request_id": _request_id(request)},
                    )
                ]
            )
        )


class _InvalidRequestModel(LLMModel):
    """模拟先加载 Skill、再用伪造 request id 调用受控 skill_run 的模型。"""

    def __init__(self, model_name: str) -> None:
        """初始化只服务单次测试回合的固定工具调用步数。"""

        super().__init__(model_name)
        self._step = 0

    @classmethod
    def supported_models(cls) -> list[str]:
        """声明测试专用模型名。"""

        return [r"invalid-request"]

    async def _generate_async_impl(
        self,
        request: Any,
        stream: bool = False,
        ctx: Any = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        """依次调用 skill_load 和携带未知标识的 skill_run，随后结束回合。"""

        del request, stream, ctx
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
                            args={"review_request_id": "review-request-not-registered"},
                        )
                    ]
                )
            )
            return
        yield LlmResponse(content=Content(parts=[Part.from_text(text="done")]))


def test_agent_calls_skill_load_then_controlled_skill_run_and_shared_pipeline() -> None:
    """验证 Agent 真实调用两个 Skill 工具，并由受控 skill_run 委托唯一 pipeline。"""

    pipeline = _Pipeline()
    agent = create_review_agent(pipeline=pipeline, skill_root=PROJECT_ROOT / "skills")

    result = agent.review(fixture="01_clean_simple")

    assert result["findings"] == [{"file": "src/a.py", "line": 1, "category": "security"}]
    assert pipeline.calls == [
        {
            "fixture": "01_clean_simple",
            "entrypoint_tool_call_count": 2,
        }
    ]
    assert agent.llm_agent.name == "code_review_agent"
    assert agent.skill_toolset.repository is agent.skill_repository
    assert agent.skill_repository.skill_list() == ["code-review"]
    assert agent.last_tool_trace == ("skill_load", "skill_run")
    assert agent.last_prompt
    assert "01_clean_simple" not in agent.last_prompt
    assert agent.agent_run_count == 1


def test_agent_exposes_only_redaction_ready_public_final_text_to_an_explicit_observer() -> None:
    """验证诊断观察器只接收 Agent 回合的公开最终文本，不改变受控工具链和 pipeline。"""

    pipeline = _Pipeline()
    observed_messages: list[str] = []
    agent = create_review_agent(pipeline=pipeline, skill_root=PROJECT_ROOT / "skills")

    agent.review(
        fixture="01_clean_simple",
        public_response_observer=observed_messages.append,
    )

    assert observed_messages == ["评审已完成，结构化报告已生成。"]
    assert agent.last_tool_trace == ("skill_load", "skill_run")
    assert len(pipeline.calls) == 1


def test_agent_rejects_skill_run_before_skill_load_without_pipeline_side_effect() -> None:
    """验证模型跳过加载门时受控 skill_run 拒绝执行，pipeline 调用数保持零。"""

    pipeline = _Pipeline()
    agent = create_review_agent(
        pipeline=pipeline,
        skill_root=PROJECT_ROOT / "skills",
        model=_SkipLoadModel("skip-load"),
    )

    with pytest.raises(AgentExecutionError, match="skill_load_required"):
        agent.review(fixture="01_clean_simple")

    assert pipeline.calls == []
    assert agent.last_tool_trace == ("skill_run",)


def test_agent_rejects_unknown_request_id_after_load_without_pipeline_side_effect() -> None:
    """验证已加载 Skill 也不能用伪造的一次性标识触发 pipeline 或沙箱副作用。"""

    pipeline = _Pipeline()
    agent = create_review_agent(
        pipeline=pipeline,
        skill_root=PROJECT_ROOT / "skills",
        model=_InvalidRequestModel("invalid-request"),
    )

    with pytest.raises(AgentExecutionError, match="skill_run_not_completed"):
        agent.review(fixture="01_clean_simple")

    assert pipeline.calls == []
    assert agent.last_tool_trace == ("skill_load", "skill_run")


def test_agent_filter_deny_persists_warning_without_sandbox_execution(tmp_path: Path) -> None:
    """验证 Agent 委托真实 pipeline 后，Filter deny 会短路沙箱并形成可查询报告。"""

    sandbox = _NoRunSandbox()
    store = SqlReviewStore(f"sqlite+pysqlite:///{(tmp_path / 'review.db').as_posix()}")
    pipeline = ReviewPipeline(
        store=store,
        governance=_DenyGovernance(),
        sandbox=sandbox,
        output_dir=tmp_path / "reports",
        task_id_factory=lambda: "agent-filter-deny",
    )
    agent = create_review_agent(pipeline=pipeline, skill_root=PROJECT_ROOT / "skills")

    try:
        result = agent.review(
            fixture=FixturePayload(
                payload_type="files",
                file_contents={"src/service.py": "def run():\n    return None\n"},
            )
        )
        bundle = store.get_task_bundle(result.task_id)
    finally:
        store.close()

    assert result.status == "completed_with_warnings"
    assert sandbox.execute_calls == 0
    assert sandbox.cleanup_calls == 1
    assert result.report["metrics"]["tool_call_count"] == 2
    assert result.report["metrics"]["filter_block_count"] == 1
    assert bundle is not None
    assert bundle["sandbox_runs"] == []
    assert len(bundle["filter_events"]) == 1
