# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""fake 模型单测：judge 解析（用 SDK 真实模板构造消息）/ agent 指令路由 / reflection 提案。

judge 测试刻意通过 ``LLMJudge`` 的真实 ``DefaultMessagesConstructor`` 生成
消息 —— SDK 若改动裁判模板（anchor 标签、rubric 行格式），这里会第一时间
报警，而不是 e2e 神秘失败。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_EXAMPLE_ROOT = _HERE.parent
_REPO_ROOT = _EXAMPLE_ROOT.parents[2]
for _p in (str(_REPO_ROOT), str(_EXAMPLE_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from trpc_agent_sdk.evaluation._eval_case import IntermediateData, Invocation  # noqa: E402
from trpc_agent_sdk.evaluation._eval_metrics import EvalMetric  # noqa: E402
from trpc_agent_sdk.evaluation._llm_judge import LLMJudge, DefaultResponseScorer  # noqa: E402
from trpc_agent_sdk.models import ModelRegistry  # noqa: E402
from trpc_agent_sdk.models._llm_request import LlmRequest  # noqa: E402
from trpc_agent_sdk.types import Content, FunctionCall, FunctionResponse, GenerateContentConfig, Part  # noqa: E402

from loop_agent.fake_models import (  # noqa: E402
    FALLBACK_ANSWER, MEMORIZE_MISS_ANSWER, FakeAgentModel, FakeJudgeModel, FakeReflectionModel, parse_directives,
    register_fake_models,
)

EVAL_CONFIG = json.loads((_EXAMPLE_ROOT / "data" / "eval_config.json").read_text(encoding="utf-8"))


def _metric(name: str) -> EvalMetric:
    entry = next(m for m in EVAL_CONFIG["metrics"] if m["metric_name"] == name)
    return EvalMetric(metric_name=name, threshold=entry["threshold"], criterion=entry["criterion"])


def _invocation(query: str, answer: str, tool_calls=(), tool_responses=()) -> Invocation:
    return Invocation(
        user_content=Content(role="user", parts=[Part.from_text(text=query)]),
        final_response=Content(role="model", parts=[Part.from_text(text=answer)]),
        intermediate_data=IntermediateData(
            tool_uses=[FunctionCall(id=f"c{i}", name=n, args=a) for i, (n, a) in enumerate(tool_calls)],
            tool_responses=[
                FunctionResponse(id=f"c{i}", name=n, response=r) for i, (n, r) in enumerate(tool_responses)
            ],
        ),
    )


def _judge_verdicts(metric_name: str, invocation: Invocation) -> dict[str, str]:
    """用 SDK 真实模板构造裁判消息 → fake judge 判定 → id→verdict。"""
    judge = LLMJudge(_metric(metric_name))
    message = judge._messages_constructor.format_user_message([invocation], [invocation], judge._criterion, metric_name)
    output = FakeJudgeModel.judge_message(message)
    items = json.loads(output)["items"]
    return {item["id"]: item["verdict"] for item in items}


class TestFakeJudge:
    """fake judge 对真实模板消息的解析与条件规则。"""

    def test_rubric_response_json_case_fails_r_json(self):
        # 用户要 JSON，回答是自由文本 → r_json no；未要求介绍 → r_cite 条件不适用 = yes
        inv = _invocation("把 3 公里换算成米，用 JSON 输出", "3 公里等于 3000 米")
        verdicts = _judge_verdicts("llm_rubric_response", inv)
        assert verdicts == {"r_json": "no", "r_cite": "yes"}

    def test_rubric_response_json_case_passes_when_json(self):
        inv = _invocation("把 3 公里换算成米，用 JSON 输出", '{"result": 3000, "unit": "m"}')
        verdicts = _judge_verdicts("llm_rubric_response", inv)
        assert verdicts == {"r_json": "yes", "r_cite": "yes"}

    def test_rubric_response_intro_case_requires_citation(self):
        inv = _invocation("介绍一下深圳", "深圳是一座很不错的城市。")
        assert _judge_verdicts("llm_rubric_response", inv)["r_cite"] == "no"
        inv_ok = _invocation("介绍一下深圳", "深圳是一座以科技创新闻名的现代化滨海城市。 [source: city-guide]")
        assert _judge_verdicts("llm_rubric_response", inv_ok)["r_cite"] == "yes"

    def test_knowledge_recall_conditional_rule(self):
        # 未要求介绍 → 条件不适用 = yes（即使没有任何检索结果）
        inv = _invocation("你叫什么名字？", "我是城市信息助手 CityInfo。")
        assert _judge_verdicts("llm_rubric_knowledge_recall", inv) == {"k_guide": "yes"}

    def test_knowledge_recall_hits_and_misses(self):
        intro = "介绍一下深圳"
        with_tool = _invocation(
            intro,
            "深圳是… [source: city-guide]",
            tool_calls=[("knowledge_search", {
                "query": "深圳"
            })],
            tool_responses=[("knowledge_search", {
                "source": "city-guide",
                "summary": "深圳是…"
            })],
        )
        assert _judge_verdicts("llm_rubric_knowledge_recall", with_tool) == {"k_guide": "yes"}
        without_tool = _invocation(intro, "深圳是一座很不错的城市。")
        assert _judge_verdicts("llm_rubric_knowledge_recall", without_tool) == {"k_guide": "no"}

    def test_output_parsable_by_sdk_scorer(self):
        """fake judge 的输出必须能被 SDK 的 DefaultResponseScorer 解析。"""
        inv = _invocation("把 3 公里换算成米，用 JSON 输出", "3 公里等于 3000 米")
        judge = LLMJudge(_metric("llm_rubric_response"))
        message = judge._messages_constructor.format_user_message([inv], [inv], judge._criterion, "llm_rubric_response")
        result = DefaultResponseScorer().parse_response(FakeJudgeModel.judge_message(message), "llm_rubric_response")
        assert result.score == 0.5  # r_json no + r_cite yes
        assert {r.id for r in result.rubric_scores} == {"r_json", "r_cite"}


def _agent_request(query: str, instruction: str, tool_response=None) -> LlmRequest:
    contents = [Content(role="user", parts=[Part.from_text(text=query)])]
    if tool_response is not None:
        name, payload = tool_response
        contents.append(
            Content(role="user",
                    parts=[Part(function_response=FunctionResponse(id="call-1", name=name, response=payload))]))
    request = LlmRequest(contents=contents, config=GenerateContentConfig())
    request.config.system_instruction = instruction
    return request


async def _agent_reply(query: str, instruction: str, tool_response=None):
    model = FakeAgentModel("fake-agent/test")
    request = _agent_request(query, instruction, tool_response)
    async for response in model._generate_async_impl(request):
        return response.content.parts
    raise AssertionError("model yielded nothing")


def _directives(**overrides) -> str:
    values = {"output_format": "plain", "unit_normalization": "off", "knowledge": "off", "memorize": "off"}
    values.update(overrides)
    lines = "\n".join(f"{k}: {v}" for k, v in values.items())
    return f"<!-- directives:\n{lines}\n-->\n# 角色\n城市信息助手"


BASELINE = _directives()
OPTIMIZED = _directives(output_format="json", unit_normalization="on", knowledge="on")
MEMORIZE = _directives(memorize="train_table")


class TestFakeAgentRouting:
    """指令 DSL × 查询类型的路由表（表驱动核心组合）。"""

    def test_parse_directives_defaults_and_comments(self):
        assert parse_directives("") == {
            "output_format": "plain",
            "unit_normalization": "off",
            "knowledge": "off",
            "memorize": "off",
        }
        parsed = parse_directives("<!-- directives:\noutput_format: json   # 行内注释\nknowledge: on\n-->")
        assert parsed["output_format"] == "json"
        assert parsed["knowledge"] == "on"
        assert parsed["memorize"] == "off"

    async def test_convert_baseline_uses_raw_unit(self):
        parts = await _agent_reply("把 3 公里换算成米，用 JSON 输出", BASELINE)
        call = parts[0].function_call
        assert call.name == "convert_distance"
        assert call.args == {"value": 3, "unit": "公里"}

    async def test_convert_optimized_normalizes_unit_and_outputs_json(self):
        parts = await _agent_reply("把 5 公里换算成米，用 JSON 输出", OPTIMIZED)
        assert parts[0].function_call.args == {"value": 5, "unit": "km"}
        parts = await _agent_reply("把 5 公里换算成米，用 JSON 输出",
                                   OPTIMIZED,
                                   tool_response=("convert_distance", {
                                       "meters": 5000
                                   }))
        assert parts[0].text == '{"result": 5000, "unit": "m"}'

    async def test_convert_baseline_plain_final(self):
        parts = await _agent_reply("把 3 公里换算成米，用 JSON 输出",
                                   BASELINE,
                                   tool_response=("convert_distance", {
                                       "error": "unsupported unit"
                                   }))
        assert parts[0].text == "3 公里等于 3000 米"

    async def test_intro_baseline_answers_from_memory(self):
        parts = await _agent_reply("介绍一下杭州", BASELINE)
        assert parts[0].text == "杭州是一座很不错的城市。"

    async def test_intro_optimized_searches_then_cites(self):
        parts = await _agent_reply("介绍一下杭州", OPTIMIZED)
        assert parts[0].function_call.name == "knowledge_search"
        assert parts[0].function_call.args == {"query": "杭州"}
        parts = await _agent_reply("介绍一下杭州",
                                   OPTIMIZED,
                                   tool_response=("knowledge_search", {
                                       "source": "city-guide",
                                       "summary": "杭州是一座名城。"
                                   }))
        assert parts[0].text == "杭州是一座名城。 [source: city-guide]"

    async def test_identity_route_is_directive_independent(self):
        for instruction in (BASELINE, OPTIMIZED):
            parts = await _agent_reply("你叫什么名字？", instruction)
            assert parts[0].text == "我是城市信息助手 CityInfo。"

    async def test_unknown_query_falls_back(self):
        parts = await _agent_reply("请自报家门", BASELINE)
        assert parts[0].text == FALLBACK_ANSWER

    async def test_memorize_replays_table_hit(self):
        parts = await _agent_reply("把 4 公里换算成米，用 JSON 输出", MEMORIZE)
        assert parts[0].function_call.name == "convert_distance"
        assert parts[0].function_call.args == {"value": 4, "unit": "km"}
        parts = await _agent_reply("把 4 公里换算成米，用 JSON 输出",
                                   MEMORIZE,
                                   tool_response=("convert_distance", {
                                       "meters": 4000
                                   }))
        assert parts[0].text == '{"result": 4000, "unit": "m"}'

    async def test_memorize_miss_gives_wrong_answer_without_tools(self):
        # 验证集问题不在查表里 → 错误答案且不调工具（过拟合在 val 上暴露的机制）
        for query in ("把 5 公里换算成米，用 JSON 输出", "你叫什么名字？", "介绍一下杭州"):
            parts = await _agent_reply(query, MEMORIZE)
            assert parts[0].function_call is None
            assert parts[0].text == MEMORIZE_MISS_ANSWER


class TestFakeReflection:
    """reflection 提案器：按 prompt-field 标记返回对应候选并带 ``` 包裹。"""

    def _reflection_prompt(self, field_text: str) -> str:
        return ("I provided an assistant with the following instructions:\n"
                f"```\n{field_text}\n```\n\nExamples and feedback:\n```\ncase feedback...\n```\n"
                "Write the new instruction within ``` blocks.")

    def test_returns_scenario_candidate_for_field(self):
        system_text = (_EXAMPLE_ROOT / "loop_agent" / "prompts" / "system.md").read_text(encoding="utf-8")
        out = FakeReflectionModel.propose(self._reflection_prompt(system_text), "success")
        assert out.startswith("```\n") and out.endswith("\n```")
        expected = (_EXAMPLE_ROOT / "candidates" / "system_prompt.success.md").read_text(encoding="utf-8").strip()
        assert out[4:-4].strip() == expected

    def test_all_scenario_candidates_exist_and_keep_markers(self):
        for field in ("system_prompt", "skill"):
            for scenario in ("success", "no_effect", "overfit"):
                path = _EXAMPLE_ROOT / "candidates" / f"{field}.{scenario}.md"
                text = path.read_text(encoding="utf-8")
                assert f"<!-- prompt-field: {field} -->" in text, path
                assert "```" not in text, f"{path} 不能包含三反引号（会破坏 gepa 提取）"

    def test_unknown_field_returns_current_text_noop(self):
        prompt = self._reflection_prompt("<!-- prompt-field: router --> 当前路由 prompt 文本")
        out = FakeReflectionModel.propose(prompt, "success")
        assert out == "```\n<!-- prompt-field: router --> 当前路由 prompt 文本\n```"


def test_registry_registration_is_idempotent():
    register_fake_models()
    register_fake_models()
    assert ModelRegistry.resolve("fake-agent/probe") is FakeAgentModel
    assert ModelRegistry.resolve("fake-judge/probe") is FakeJudgeModel
    assert ModelRegistry.resolve("fake-reflection/probe") is FakeReflectionModel
    # create_model 路由（判官/反思 LM 的 provider_name 走的就是这条路径）
    model = ModelRegistry.create_model("fake-reflection/success", api_key="", base_url="")
    assert isinstance(model, FakeReflectionModel)
    assert model.scenario == "success"
