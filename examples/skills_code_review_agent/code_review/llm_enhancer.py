#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Optional fake, real, or disabled report enhancement."""

from __future__ import annotations

import asyncio
import json
import os
import warnings
from collections.abc import AsyncGenerator, Mapping
from copy import deepcopy
from typing import Any

from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message=r"The default value of `allowed_objects` will change.*",
    category=LangChainPendingDeprecationWarning,
)


def _import_sdk_agent_dependencies() -> tuple[Any, Any, Any, Any, Any, Any, Any, Any]:
    """在 SDK 延迟导入期间仅过滤已知无路径价值的弃用警告，其余警告保持原样。"""

    original_showwarning = warnings.showwarning

    def safe_showwarning(
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: Any = None,
        line: str | None = None,
    ) -> None:
        """丢弃唯一已知的 LangGraph 弃用提示，避免其携带 site-packages 绝对路径进入终端。"""

        if (
            issubclass(category, LangChainPendingDeprecationWarning)
            and str(message).startswith("The default value of `allowed_objects` will change")
        ):
            return
        original_showwarning(message, category, filename, lineno, file, line)

    warnings.showwarning = safe_showwarning
    try:
        from trpc_agent_sdk.agents import LlmAgent
        from trpc_agent_sdk.models import LLMModel, LlmResponse, OpenAIModel
        from trpc_agent_sdk.runners import Runner
        from trpc_agent_sdk.sessions import InMemorySessionService
        from trpc_agent_sdk.types import Content, Part

        return LlmAgent, LLMModel, LlmResponse, OpenAIModel, Runner, InMemorySessionService, Content, Part
    finally:
        warnings.showwarning = original_showwarning


(
    LlmAgent,
    LLMModel,
    LlmResponse,
    OpenAIModel,
    Runner,
    InMemorySessionService,
    Content,
    Part,
) = _import_sdk_agent_dependencies()

from agent.prompts import ENHANCEMENT_INSTRUCTION
from code_review.model_runtime import build_real_model
from code_review.redaction import redact_data


_MODES = frozenset({"off", "fake", "real"})
_IDENTITY_FIELDS = frozenset(
    {
        "severity",
        "category",
        "file",
        "line",
        "title",
        "evidence",
        "confidence",
        "source",
        "rule_id",
        "bucket",
        "dedup_key",
    }
)


class _FakeEnhancementModel(LLMModel):
    """提供离线固定文本的 SDK 模型，用于验证与 real 相同的调用链。"""

    @classmethod
    def supported_models(cls) -> list[str]:
        """声明本 fake 模型仅服务于本项目的固定名称。"""

        return [r"code-review-fake"]

    async def _generate_async_impl(
        self,
        _request: Any,
        stream: bool = False,
        ctx: Any = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        """返回不依赖网络或密钥的固定 JSON 文本。"""

        del stream, ctx
        payload = {
            "summary": "已生成脱敏的人工复核摘要。",
            "recommendation": "请在隔离分支完成修复并补充对应回归测试。",
            "human_review_hint": "请人工确认修复后的风险边界。",
        }
        yield LlmResponse(content=Content(parts=[Part.from_text(text=json.dumps(payload, ensure_ascii=False))]))

    def validate_request(self, request: Any) -> None:
        """沿用基类请求校验，确保 fake 与 real 都拒绝空输入。"""

        super().validate_request(request)


class LlmEnhancer:
    """通过 LlmAgent 与 Runner 受限增强报告文本，永不参与 finding 检出。"""

    def __init__(
        self,
        *,
        mode: str = "off",
        model: LLMModel | None = None,
        environ: Mapping[str, str] | None = None,
        timeout_seconds: float = 110.0,
    ) -> None:
        """初始化显式模式；real 仅使用调用方提供的受控模型环境或进程环境。"""

        if mode not in _MODES:
            raise ValueError("model_mode_invalid")
        if mode == "off" and model is not None:
            raise ValueError("model_mode_off_rejects_model")
        if timeout_seconds <= 0:
            raise ValueError("llm_timeout_seconds_invalid")
        self._mode = mode
        self._model = model
        self._environment = dict(environ) if environ is not None else None
        self._timeout_seconds = float(timeout_seconds)
        self.last_prompt = ""
        self.agent_run_count = 0

    @property
    def mode(self) -> str:
        """返回当前显式模型模式，供调用方审计而不暴露模型配置。"""

        return self._mode

    def enhance(self, report: Mapping[str, Any]) -> dict[str, Any]:
        """运行受控模型链路并仅合并 recommendation、summary 与人工复核提示文本。"""

        baseline = deepcopy(dict(report))
        if self._mode == "off":
            return baseline
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("llm_enhancement_sync_api_requires_no_running_loop")
        prompt = self._build_prompt(baseline)
        self.last_prompt = prompt
        response = asyncio.run(
            asyncio.wait_for(
                self._run_agent(prompt),
                timeout=self._timeout_seconds,
            )
        )
        return self._merge_text_only(baseline, response)

    def _build_prompt(self, report: Mapping[str, Any]) -> str:
        """从完整脱敏报告提取最小文本增强上下文，禁止传入原始 diff 或环境值。"""

        findings = []
        for bucket in ("findings", "needs_human_review"):
            for finding in report.get(bucket, ()):
                if not isinstance(finding, Mapping):
                    continue
                findings.append(
                    {
                        "bucket": bucket,
                        "severity": finding.get("severity"),
                        "category": finding.get("category"),
                        "title": finding.get("title"),
                        "recommendation": finding.get("recommendation"),
                    }
                )
        payload = redact_data(
            {
                "task": "enhance_existing_review_text_only",
                "findings": findings,
                "summary": report.get("final_conclusion", {}).get("summary", "")
                if isinstance(report.get("final_conclusion"), Mapping)
                else "",
            }
        )
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    async def _run_agent(self, prompt: str) -> Mapping[str, Any]:
        """让 fake 与 real 共享 LlmAgent+Runner 会话执行路径，并解析最后一段 JSON 响应。"""

        model = self._resolve_model()
        agent = LlmAgent(
            name="code_review_enhancer",
            model=model,
            instruction=ENHANCEMENT_INSTRUCTION,
            include_contents="default",
        )
        service = InMemorySessionService()
        runner = Runner(
            app_name="code_review_enhancer",
            agent=agent,
            session_service=service,
            enable_post_turn_processing=False,
        )
        response_text = ""
        try:
            message = Content(parts=[Part.from_text(text=prompt)])
            async for event in runner.run_async(
                user_id="code_review",
                session_id="enhancement",
                new_message=message,
            ):
                if event.content is None:
                    continue
                for part in event.content.parts:
                    if part.text:
                        response_text = part.text
            self.agent_run_count += 1
        finally:
            await runner.close()
        try:
            parsed = json.loads(response_text)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, Mapping) else {}

    def _resolve_model(self) -> LLMModel:
        """解析显式注入模型、离线 fake 或明确 real 环境配置，绝不自动升级为 real。"""

        if self._model is not None:
            return self._model
        if self._mode == "fake":
            return _FakeEnhancementModel("code-review-fake")
        if self._mode != "real":
            raise ValueError("model_mode_off_has_no_model")
        environment = os.environ if self._environment is None else self._environment
        api_key = environment.get("TRPC_AGENT_API_KEY", "")
        base_url = environment.get("TRPC_AGENT_BASE_URL", "")
        model_name = environment.get("TRPC_AGENT_MODEL_NAME", "")
        if not api_key or not base_url or not model_name:
            raise ValueError("real_model_configuration_missing")
        return build_real_model(environment)

    def _merge_text_only(
        self,
        baseline: dict[str, Any],
        response: Mapping[str, Any],
    ) -> dict[str, Any]:
        """将模型输出限制为文本字段，并验证模型无法改变任何 finding 身份或桶归属。"""

        recommendation = response.get("recommendation")
        summary = response.get("summary")
        hint = response.get("human_review_hint")
        if not isinstance(recommendation, str) or not recommendation.strip():
            recommendation = None
        if not isinstance(summary, str) or not summary.strip():
            summary = None
        if not isinstance(hint, str) or not hint.strip():
            hint = None
        for bucket in ("findings", "needs_human_review"):
            findings = baseline.get(bucket, ())
            if not isinstance(findings, list):
                continue
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                identity = {name: deepcopy(finding.get(name)) for name in _IDENTITY_FIELDS}
                if recommendation is not None:
                    finding["recommendation"] = redact_data(recommendation)
                if bucket == "needs_human_review" and hint is not None:
                    finding["recommendation"] = redact_data(f"{finding['recommendation']} {hint}")
                if any(finding.get(name) != value for name, value in identity.items()):
                    raise ValueError("llm_attempted_to_mutate_finding_identity")
        conclusion = baseline.get("final_conclusion")
        if isinstance(conclusion, dict) and summary is not None:
            conclusion["summary"] = redact_data(summary)
            recommendations = conclusion.get("recommendations")
            if isinstance(recommendations, list) and recommendation is not None:
                conclusion["recommendations"] = [redact_data(recommendation), *recommendations]
        return baseline


__all__ = ["LlmEnhancer"]
