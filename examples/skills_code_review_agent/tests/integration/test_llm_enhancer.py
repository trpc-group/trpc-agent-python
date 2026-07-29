#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Integration tests for the constrained LLM enhancement boundary."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from code_review.llm_enhancer import LlmEnhancer  # noqa: E402
from trpc_agent_sdk.models import LLMModel, LlmResponse  # noqa: E402
from trpc_agent_sdk.types import Content, Part  # noqa: E402


class _InjectedRealModel(LLMModel):
    """模拟由调用方显式注入的 real 模型，避免测试访问网络或读取真实 Key。"""

    @classmethod
    def supported_models(cls) -> list[str]:
        """声明测试专用模型名称。"""

        return [r"injected-real"]

    async def _generate_async_impl(
        self,
        _request: Any,
        stream: bool = False,
        ctx: Any = None,
    ) -> Any:
        """返回与 fake 格式相同的 JSON，验证模式差异不会改变 Runner 调用协议。"""

        del stream, ctx
        yield LlmResponse(
            content=Content(
                parts=[
                    Part.from_text(
                        text='{"summary":"real 摘要","recommendation":"real 建议"}'
                    )
                ]
            )
        )

    def validate_request(self, request: Any) -> None:
        """沿用 SDK 请求校验，确保 injected real 与 fake 一样走标准模型边界。"""

        super().validate_request(request)


def _report(secret: str) -> dict[str, object]:
    """构造带合成凭据的 canonical 报告，验证传给模型前会统一脱敏。"""

    finding = {
        "severity": "high",
        "category": "secrets",
        "file": "config/.env",
        "line": 1,
        "title": "敏感凭据泄漏",
        "evidence": f"TOKEN={secret}",
        "recommendation": "轮换凭据。",
        "confidence": 0.99,
        "source": "rule-engine",
        "rule_id": "secrets.github-pat",
        "bucket": "findings",
        "dedup_key": "config/.env:1:secrets",
        "extra": {"also_matched": []},
    }
    return {
        "findings": [finding],
        "needs_human_review": [],
        "final_conclusion": {
            "summary": "发现问题。",
            "recommendations": ["轮换凭据。"],
        },
    }


def test_fake_enhancement_redacts_model_input_and_only_changes_text_fields() -> None:
    """验证 fake 走真实 Agent/Runner 链路且不修改 finding 身份字段。"""

    secret = "ghp_" + "a" * 36
    original = _report(secret)
    enhancer = LlmEnhancer(mode="fake")
    enhanced = enhancer.enhance(copy.deepcopy(original))

    before = original["findings"][0]
    after = enhanced["findings"][0]
    identity_fields = (
        "severity", "category", "file", "line", "title", "evidence",
        "confidence", "source", "rule_id", "bucket", "dedup_key",
    )
    for name in identity_fields:
        assert after[name] == before[name]
    assert after["recommendation"] != before["recommendation"]
    assert enhanced["final_conclusion"]["summary"] != original["final_conclusion"]["summary"]
    assert secret not in enhancer.last_prompt
    assert enhancer.agent_run_count == 1


def test_explicit_real_mode_uses_the_same_agent_runner_path() -> None:
    """验证 real 只能显式选择，且注入模型仍通过与 fake 相同的 Agent/Runner 路径。"""

    enhancer = LlmEnhancer(mode="real", model=_InjectedRealModel("injected-real"))
    enhanced = enhancer.enhance(_report("ghp_" + "b" * 36))

    assert enhanced["findings"][0]["recommendation"] == "real 建议"
    assert enhanced["final_conclusion"]["summary"] == "real 摘要"
    assert enhancer.agent_run_count == 1
