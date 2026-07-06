# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""三个确定性 fake 模型：让整条「评测→优化→回归」闭环零 API Key 可复现。

设计动机
--------
本 example 的验收要求是「fake judge / fake model / trace mode 下完整 pipeline
≤ 3 分钟、无真实 API Key」。框架的 ``ModelRegistry`` 按模型名正则路由到
``LLMModel`` 子类，而 judge / reflection LM 的配置都支持 ``provider_name``
（非 openai 的 provider 会走 ``ModelRegistry.create_model("{provider}/{model}")``），
所以这里注册三个 fake provider，**不改任何 SDK 代码** 就能把整条链路换成
规则驱动的确定性实现：

- ``fake-agent/*``     → :class:`FakeAgentModel`      被评测的 agent 本体
- ``fake-judge/*``     → :class:`FakeJudgeModel`      LLM rubric 裁判
- ``fake-reflection/*``→ :class:`FakeReflectionModel` GEPA 的反思 LM

FakeAgentModel：指令敏感的规则 agent
------------------------------------
真实场景里 prompt 改动会改变模型行为；离线演示要复现这一点，就让 fake
agent 从 system instruction（= system.md + skill.md 拼合文本）里解析一个
「指令 DSL」HTML 注释块::

    <!-- directives:
    output_format: plain|json    # 换算问题是否输出规范 JSON
    unit_normalization: off|on   # 工具参数 unit 是否归一化为 "km"
    knowledge: off|on            # 城市介绍是否先调 knowledge_search 并标注来源
    memorize: off|train_table    # 过拟合开关：查表复读训练/调参集样本
    -->

于是「优化 prompt」（改写 system.md）会真实地改变 agent 行为，评测分数
随之变化 —— GEPA 的整个反思循环得以在离线环境下端到端运转。
``memorize: train_table`` 是刻意设计的过拟合候选：查表复读训练/调参集的
标准轨迹与答案，未命中的问题给出错误答案，从而制造「训练集提升、验证集
退化」的必拒场景。

FakeJudgeModel：规则化 rubric 裁判
----------------------------------
只解析 **user 角色消息**（system instruction 里含格式说明文本，会造成误
判），取最后一个 ``<rubric>`` 块，逐行按 ``id: text`` 拆出 rubric。
rubric 文本用反引号 token 驱动判定（与真实裁判的「条件规则」对齐）：

- 文本含「如果」→ 第一个反引号 token 是条件关键词，先在 ``<main_prompt>``
  里查条件；条件不适用 → verdict "yes"（对齐真实 judge prompt 的
  conditional-rubric 规则：not applicable => yes）。
- 其余 token 必须**全部**出现在判定目标里：``llm_rubric_response`` 的目标
  是 ``<final_answer>``；``llm_rubric_knowledge_recall`` 的目标是
  ``<retrieved_knowledge>``（由消息里是否出现该块自动识别）。

输出与真实裁判完全同构：``{"items": [{"id","rubric","evidence","reason",
"verdict"}]}``，走 SDK 自带的 ``DefaultResponseScorer`` 解析。

FakeReflectionModel：查表式候选提案器
-------------------------------------
gepa 的反思模板会把**当前 prompt 文本**嵌进反思请求（``<curr_param>``），
且从回复的第一对与最后一对三反引号之间提取新 prompt。每个 prompt 文件
顶部有 ``<!-- prompt-field: <名字> -->`` 标记，反思模型据此识别是在改写
哪个字段，然后返回 ``candidates/<字段>.<场景>.md`` 的内容（场景 = 自己
model_name 里 ``/`` 之后的部分，如 ``fake-reflection/success`` → success）。
找不到候选文件时原样返回当前文本（无害的 no-op 提案）。

注册与幂等
----------
``register_fake_models()`` 在本模块 import 时执行一次；探测用固定字符串
（``ModelRegistry.resolve`` 带 lru_cache，固定串保证缓存命中一致）。
三个类都有类级计数器（``calls``），供 pipeline 报告 fake 模型调用量；
自增走模块级锁（gepa 会在工作线程里并发调用，裸 ``+=`` 非原子）。
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, AsyncGenerator, List, Optional

from trpc_agent_sdk.models import LLMModel, ModelRegistry
from trpc_agent_sdk.models._llm_request import LlmRequest
from trpc_agent_sdk.models._llm_response import LlmResponse
from trpc_agent_sdk.types import Content, FunctionCall, Part

from .tools import CITY_CORPUS

_EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_DIR = _EXAMPLE_ROOT / "candidates"

# 类级 calls 计数器的自增锁：gepa 在工作线程里并发调模型，``+=`` 是
# 读-改-写三步、GIL 下也可能丢更新；计数只是报告用的信息性字段，但
# 既然一把锁就能保证准确，就没有理由留下竞态。
_CALLS_LOCK = threading.Lock()


def _bump_calls(model_cls: type) -> None:
    """线程安全地把 ``model_cls.calls`` 加一。"""
    with _CALLS_LOCK:
        model_cls.calls += 1


# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------


def _text_response(text: str) -> LlmResponse:
    """把纯文本包装成一条非流式 LlmResponse。"""
    return LlmResponse(content=Content(role="model", parts=[Part.from_text(text=text)]))


def _last_user_text(request: LlmRequest) -> str:
    """取最后一条 user 角色消息的纯文本（多 part 拼接）。"""
    last = ""
    for content in request.contents or []:
        if content.role != "user" or not content.parts:
            continue
        text = "\n".join(p.text for p in content.parts if p.text)
        if text.strip():
            last = text
    return last


def _first_user_text(request: LlmRequest) -> str:
    """取第一条带文本的 user 消息（评测会话里即用户原始问题）。"""
    for content in request.contents or []:
        if content.role != "user" or not content.parts:
            continue
        text = "\n".join(p.text for p in content.parts if p.text)
        if text.strip():
            return text
    return ""


def _has_function_response(request: LlmRequest) -> bool:
    """判断会话里是否已有工具返回（即当前是「工具调用后」的第二跳）。"""
    for content in request.contents or []:
        for part in content.parts or []:
            if part.function_response is not None:
                return True
    return False


def _last_function_response(request: LlmRequest) -> Optional[dict]:
    """取最后一个 function_response 的 response dict；没有则 None。"""
    result: Optional[dict] = None
    for content in request.contents or []:
        for part in content.parts or []:
            if part.function_response is not None:
                resp = part.function_response.response
                if isinstance(resp, dict):
                    result = resp
    return result


def _format_number(value: float) -> str:
    """整数值不带小数点（3.0 → "3"），其余按原样。"""
    if float(value).is_integer():
        return str(int(value))
    return str(value)


# ---------------------------------------------------------------------------
# FakeAgentModel
# ---------------------------------------------------------------------------

# 过拟合查表：key = 训练集/调参集(probe)的原始问题；value = 标准轨迹 + 标准答案。
# 覆盖 train.evalset.json 的 3 条与 optimizer_probe.evalset.json 的 3 条；
# 验证集(val)的问题刻意不入表 → memorize 候选在 val 上必然答错。
MEMORIZE_TABLE: dict[str, dict[str, Any]] = {
    "把 3 公里换算成米，用 JSON 输出": {
        "tool_calls": [("convert_distance", {
            "value": 3,
            "unit": "km"
        })],
        "final": '{"result": 3000, "unit": "m"}',
    },
    "介绍一下深圳": {
        "tool_calls": [("knowledge_search", {
            "query": "深圳"
        })],
        "final": f"{CITY_CORPUS['深圳']} [source: city-guide]",
    },
    "你的名字是什么？": {
        "tool_calls": [],
        "final": "我是城市信息助手 CityInfo。",
    },
    "把 4 公里换算成米，用 JSON 输出": {
        "tool_calls": [("convert_distance", {
            "value": 4,
            "unit": "km"
        })],
        "final": '{"result": 4000, "unit": "m"}',
    },
    "请介绍一下深圳": {
        "tool_calls": [("knowledge_search", {
            "query": "深圳"
        })],
        "final": f"{CITY_CORPUS['深圳']} [source: city-guide]",
    },
    "请自报家门": {
        "tool_calls": [],
        "final": "我是城市信息助手 CityInfo。",
    },
}

# memorize 候选对没见过的问题给出的错误答案（触发 val 退化）
MEMORIZE_MISS_ANSWER = "根据以往训练经验，答案与训练样本一致。"

# 未匹配任何路由时的兜底回答（baseline 在 probe_identity 上因此失败）
FALLBACK_ANSWER = "抱歉，我暂时无法理解这个问题。"

_DIRECTIVES_RE = re.compile(r"<!--\s*directives:\s*\n(.*?)-->", re.DOTALL)
_KM_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*公里")

DEFAULT_DIRECTIVES = {
    "output_format": "plain",
    "unit_normalization": "off",
    "knowledge": "off",
    "memorize": "off",
}


def parse_directives(instruction: str) -> dict[str, str]:
    """从 system instruction 里解析指令 DSL；缺失项用 baseline 默认值。"""
    directives = dict(DEFAULT_DIRECTIVES)
    match = _DIRECTIVES_RE.search(instruction or "")
    if not match:
        return directives
    for line in match.group(1).splitlines():
        line = line.split("#", 1)[0].strip()  # 去掉行内注释
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key in directives and value:
            directives[key] = value
    return directives


class FakeAgentModel(LLMModel):
    """指令敏感的规则 agent 模型（见模块 docstring 的路由表）。"""

    calls: int = 0  # 类级计数器：pipeline 报告 fake 模型调用量

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"fake-agent/.*"]

    async def _generate_async_impl(self,
                                   request: LlmRequest,
                                   stream: bool = False,
                                   ctx=None) -> AsyncGenerator[LlmResponse, None]:
        _bump_calls(type(self))
        directives = parse_directives(str(request.config.system_instruction or "") if request.config else "")
        query = _first_user_text(request)
        after_tool = _has_function_response(request)

        # --- memorize=train_table：查表复读（过拟合候选） ---
        if directives["memorize"] == "train_table":
            yield self._memorized_response(query, after_tool)
            return

        # --- 换算路由 ---
        km_match = _KM_RE.search(query)
        if "换算成米" in query and km_match:
            value = float(km_match.group(1))
            if not after_tool:
                unit = "km" if directives["unit_normalization"] == "on" else "公里"
                args_value: Any = int(value) if value.is_integer() else value
                yield self._tool_call_response("convert_distance", {"value": args_value, "unit": unit})
                return
            meters = int(value * 1000) if (value * 1000).is_integer() else value * 1000
            if directives["output_format"] == "json":
                yield _text_response(f'{{"result": {meters}, "unit": "m"}}')
            else:
                yield _text_response(f"{_format_number(value)} 公里等于 {meters} 米")
            return

        # --- 城市介绍路由 ---
        city = next((c for c in CITY_CORPUS if c in query), None)
        if "介绍" in query and city is not None:
            if directives["knowledge"] != "on":
                yield _text_response(f"{city}是一座很不错的城市。")
                return
            if not after_tool:
                yield self._tool_call_response("knowledge_search", {"query": city})
                return
            tool_resp = _last_function_response(request) or {}
            summary = str(tool_resp.get("summary") or "")
            if summary:
                yield _text_response(f"{summary} [source: city-guide]")
            else:
                yield _text_response(f"{city}的资料暂缺。")
            return

        # --- 身份路由 ---
        if "名字" in query:
            yield _text_response("我是城市信息助手 CityInfo。")
            return

        yield _text_response(FALLBACK_ANSWER)

    def _memorized_response(self, query: str, after_tool: bool) -> LlmResponse:
        """memorize=train_table 分支：命中查表复读，未命中给错误答案。"""
        entry = MEMORIZE_TABLE.get(query.strip())
        if entry is None:
            return _text_response(MEMORIZE_MISS_ANSWER)
        if entry["tool_calls"] and not after_tool:
            parts = [
                Part(function_call=FunctionCall(id=f"memo-{i}", name=name, args=dict(args)))
                for i, (name, args) in enumerate(entry["tool_calls"])
            ]
            return LlmResponse(content=Content(role="model", parts=parts))
        return _text_response(entry["final"])

    @staticmethod
    def _tool_call_response(name: str, args: dict[str, Any]) -> LlmResponse:
        return LlmResponse(
            content=Content(role="model", parts=[Part(function_call=FunctionCall(id="call-1", name=name, args=args))]))


# ---------------------------------------------------------------------------
# FakeJudgeModel
# ---------------------------------------------------------------------------

_RUBRIC_BLOCK_RE = re.compile(r"<rubric>\s*(.*?)\s*</rubric>", re.DOTALL)
_MAIN_PROMPT_RE = re.compile(r"<main_prompt>\s*(.*?)\s*</main_prompt>", re.DOTALL)
_FINAL_ANSWER_RE = re.compile(r"<final_answer>\s*(.*?)\s*</final_answer>", re.DOTALL)
_KNOWLEDGE_RE = re.compile(r"<retrieved_knowledge>\s*(.*?)\s*</retrieved_knowledge>", re.DOTALL)
_BACKTICK_TOKEN_RE = re.compile(r"`([^`]+)`")


class FakeJudgeModel(LLMModel):
    """规则化 rubric 裁判（DSL 见模块 docstring）。"""

    calls: int = 0

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"fake-judge/.*"]

    async def _generate_async_impl(self,
                                   request: LlmRequest,
                                   stream: bool = False,
                                   ctx=None) -> AsyncGenerator[LlmResponse, None]:
        _bump_calls(type(self))
        # 只看 user 消息：system instruction 里带格式示例文本，会干扰解析
        message = _last_user_text(request)
        yield _text_response(self.judge_message(message))

    @classmethod
    def judge_message(cls, message: str) -> str:
        """对一条裁判消息给出 items JSON（纯函数，便于单测）。"""
        rubric_matches = _RUBRIC_BLOCK_RE.findall(message)
        rubrics_block = rubric_matches[-1] if rubric_matches else ""
        main_prompt = cls._last_group(_MAIN_PROMPT_RE, message)
        knowledge_match = _KNOWLEDGE_RE.search(message)
        if knowledge_match is not None:
            target = knowledge_match.group(1).strip()
        else:
            target = cls._last_group(_FINAL_ANSWER_RE, message)

        items = []
        for line in rubrics_block.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            rubric_id, _, rubric_text = line.partition(":")
            verdict, reason = cls._verdict(rubric_text.strip(), main_prompt, target)
            items.append({
                "id": rubric_id.strip(),
                "rubric": rubric_text.strip(),
                "evidence": target[:80],
                "reason": reason,
                "verdict": verdict,
            })
        return json.dumps({"items": items}, ensure_ascii=False)

    @staticmethod
    def _verdict(rubric_text: str, main_prompt: str, target: str) -> tuple[str, str]:
        """按反引号 token DSL 判定单条 rubric。"""
        tokens = _BACKTICK_TOKEN_RE.findall(rubric_text)
        if not tokens:
            return "yes", "该 rubric 未定义判定 token，默认通过"
        if "如果" in rubric_text:
            condition, tokens = tokens[0], tokens[1:]
            if condition not in main_prompt:
                return "yes", f"条件「{condition}」不适用于该问题（not applicable => yes）"
        missing = [t for t in tokens if t not in target]
        if missing:
            return "no", f"判定目标中缺少关键内容：{'、'.join(missing)}"
        return "yes", "全部关键内容均在判定目标中出现"

    @staticmethod
    def _last_group(pattern: re.Pattern, message: str) -> str:
        matches = pattern.findall(message)
        return matches[-1].strip() if matches else ""


# ---------------------------------------------------------------------------
# FakeReflectionModel
# ---------------------------------------------------------------------------

_PROMPT_FIELD_RE = re.compile(r"<!--\s*prompt-field:\s*([A-Za-z_][A-Za-z0-9_]*)\s*-->")


class FakeReflectionModel(LLMModel):
    """查表式候选提案器（GEPA 反思 LM 的离线替身）。"""

    calls: int = 0

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"fake-reflection/.*"]

    @property
    def scenario(self) -> str:
        """model_name「fake-reflection/<场景>」里的场景名。"""
        return self.name.rsplit("/", 1)[-1]

    async def _generate_async_impl(self,
                                   request: LlmRequest,
                                   stream: bool = False,
                                   ctx=None) -> AsyncGenerator[LlmResponse, None]:
        _bump_calls(type(self))
        prompt = _last_user_text(request)
        yield _text_response(self.propose(prompt, self.scenario))

    @staticmethod
    def propose(prompt: str, scenario: str) -> str:
        """给定反思请求文本，返回带三反引号包裹的候选 prompt。

        gepa 从回复的第一对与最后一对 ``\\`\\`\\``` 之间提取候选文本，
        所以候选内容自身不能包含三反引号（candidates/ 目录已遵守）。
        """
        field_match = _PROMPT_FIELD_RE.search(prompt)
        candidate_text: Optional[str] = None
        if field_match is not None:
            candidate_file = CANDIDATES_DIR / f"{field_match.group(1)}.{scenario}.md"
            if candidate_file.is_file():
                candidate_text = candidate_file.read_text(encoding="utf-8").strip()
        if candidate_text is None:
            # 找不到字段/候选文件：原样返回当前 prompt（第一段 ``` 块），无害 no-op
            current = FakeReflectionModel._extract_first_code_block(prompt)
            candidate_text = current if current else prompt.strip()
        return f"```\n{candidate_text}\n```"

    @staticmethod
    def _extract_first_code_block(prompt: str) -> str:
        start = prompt.find("```")
        if start < 0:
            return ""
        end = prompt.find("```", start + 3)
        if end < 0:
            return ""
        return prompt[start + 3:end].strip()


# ---------------------------------------------------------------------------
# 注册（幂等）
# ---------------------------------------------------------------------------


def register_fake_models() -> None:
    """把三个 fake provider 注册进 ModelRegistry；重复调用无害。

    注意 ``ModelRegistry.resolve`` 带 lru_cache —— 探测必须用固定字符串，
    保证「已注册」判定与缓存条目一致。
    """
    for probe, model_cls in (
        ("fake-agent/probe", FakeAgentModel),
        ("fake-judge/probe", FakeJudgeModel),
        ("fake-reflection/probe", FakeReflectionModel),
    ):
        try:
            ModelRegistry.resolve(probe)
        except ValueError:
            ModelRegistry.register(model_cls)


register_fake_models()
