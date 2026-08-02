"""Trace 回放评测器 — 比较期望对话与实际回放，判定 case 通过/失败并归类。

设计目标：与 SDK 的 AgentEvaluator 语义对齐（`conversation` = 期望，
`actual_conversation` = 实际回放，评分含 `final_response_avg_score` 的
`contains` 匹配），同时修复旧 fake 模式"有 conversation 即通过"的空转问题。

比较采用分层规则（顺序关键）：
    1. 无 conversation → missing_expected_output；无 actual_conversation → pass（legacy 兼容）
    2. 纯数字期望 → 数值相等（容差 1e-6）
    3. 类答案期望（≤20 字符）→ 归一化 contains；纯数字期望带舍入容差兜底（带单位绝不触发）
    4. 类解释期望（>20 字符）→ contains 或期望数字子集匹配
    5. 格式层：prompt 要求 ONLY-number/JSON/markdown 而实际带额外 prose → format_not_as_required
    6. 工具层：期望含 intermediate_data 时比较 tool 名/参/结果 → tool_call_error 等
    7. 类别优先级：format > tool_parameter > wrong_tool > tool_call
       > final_response_mismatch > missing_expected_output > unknown

所有函数均为纯 Python、零外部依赖，保证 fake/trace 模式离线可跑。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# ─────────────────────────────────────────────────────────────────────
# Section: 类别枚举（与 attribution.py 的 FailureCategory 保持同名兼容）
# ─────────────────────────────────────────────────────────────────────


class FailureCategory(str):
    """失败根因类别。与 SDK 的失败归因体系对齐。"""

    FINAL_RESPONSE_MISMATCH = "final_response_mismatch"
    TOOL_CALL_ERROR = "tool_call_error"
    WRONG_TOOL_SELECTED = "wrong_tool_selected"
    TOOL_PARAMETER_ERROR = "tool_parameter_error"
    LLM_RUBRIC_NOT_MET = "llm_rubric_not_met"
    KNOWLEDGE_RECALL_INSUFFICIENT = "knowledge_recall_insufficient"
    FORMAT_NOT_AS_REQUIRED = "format_not_as_required"
    MISSING_EXPECTED_OUTPUT = "missing_expected_output"
    UNKNOWN = "unknown"


# 类别优先级：数字越小越优先。用于同一个失败命中的多个类别时选择根因。
_CATEGORY_PRIORITY = {
    FailureCategory.FORMAT_NOT_AS_REQUIRED: 0,
    FailureCategory.TOOL_PARAMETER_ERROR: 1,
    FailureCategory.WRONG_TOOL_SELECTED: 2,
    FailureCategory.TOOL_CALL_ERROR: 3,
    FailureCategory.FINAL_RESPONSE_MISMATCH: 4,
    FailureCategory.MISSING_EXPECTED_OUTPUT: 5,
    FailureCategory.UNKNOWN: 6,
}


# ─────────────────────────────────────────────────────────────────────
# Section: 文本归一化与数字提取
# ─────────────────────────────────────────────────────────────────────

# 归一化时要剥离的字符：常见标点、货币符号、百分号、空白。
_STRIP_CHARS = set("，。、；：？！()（）[]【】{}《》<>「」『』·~`!@#$%^&*_+=|\\/\"' \t\n\r,.;:?-—–")

# 归一化时**保留**字符：数字、点（小数）、负号、字母、CJK、百分号边界。
# 注意：`-` 和 `.` 不能进剥离集，否则负数和小数会被破坏。


def normalize_text(text: str) -> str:
    """NFKC 归一化 + 小写 + 剥离常见标点空白（保留数字/点/负号/CJK/字母）。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = text.lower()
    # 保留数字、字母、CJK、点、负号、百分号；剥离其余标点空白
    keep = re.compile(r"[^0-9a-z一-鿿.%-]")
    text = keep.sub("", text)
    return text


def extract_numbers(text: str) -> list[float]:
    """从文本中提取所有数字（支持小数、负数）。"""
    if not text:
        return []
    # 匹配可选负号 + 数字 + 可选小数部分
    pattern = re.compile(r"-?\d+(?:\.\d+)?")
    return [float(m) for m in pattern.findall(str(text))]


def bare_answer(text: str) -> float | None:
    """若文本是一个"裸答案"（几乎只有数字/符号），返回其数值。

    用于判断期望答案是否为纯数字（如 "42"、"$26.99"、"12%"）。
    剥离货币符号、百分号、逗号、空白后，若只剩一个数字则返回之。
    无法解析或含非数字内容 → None。
    """
    if not text:
        return None
    stripped = re.sub(r"[\s$%€£,，]", "", str(text))
    # 允许尾部单位词（如 "厘米"、"元"）存在？不——裸答案严格要求纯数字。
    if re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
        return float(stripped)
    return None


def _last_number(text: str) -> float | None:
    """返回文本中最后一个数字（用于实际回复的答案定位）。"""
    nums = extract_numbers(text)
    return nums[-1] if nums else None


def _first_number(text: str) -> float | None:
    """返回文本中第一个数字（用于期望答案定位）。"""
    nums = extract_numbers(text)
    return nums[0] if nums else None


def _has_number(text: str) -> bool:
    """判断文本是否含至少一个数字。"""
    return bool(extract_numbers(text))


def _round_close(a: float, b: float, rel_tol: float = 0.01) -> bool:
    """相对舍入容差比较（默认 1%）。用于期望带单位的四舍五入差异。"""
    if b == 0:
        return abs(a - b) <= 1e-6
    return abs(a - b) / abs(b) <= rel_tol


def _unit_of(text: str) -> str:
    """提取期望文本中数值后面的单位部分（归一化后）。

    例如 "785.40 cubic cm" → "cubiccm"，"$26.99" → ""（无单位），"48厘米" → "厘米"。
    找不到单位返回空串。
    """
    norm = normalize_text(text)
    m = re.search(r"-?\d+(?:\.\d+)?(.*)$", norm)
    if not m:
        return ""
    return m.group(1).strip()


def _unit_is_word(text: str) -> bool:
    """判断期望是否带"真实单位词"（数字后的后缀不含数字）。

    单位词由字母/CJK/百分号组成（如 cubiccm、厘米、%），不含数字。
    "3/4" → 后缀空；"30 and 20" → 后缀 "and20" 含数字 → False；
    "785.40 cubic cm" → "cubiccm" → True；"48厘米" → "厘米" → True。
    """
    unit = _unit_of(text)
    if not unit:
        return False
    return not any(ch.isdigit() for ch in unit)


def _is_numeric_only(text: str) -> bool:
    """判断文本归一化后是否纯数字（允许小数/负数）。"""
    if not text:
        return False
    stripped = re.sub(r"[\s$%€£,，]", "", str(text))
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", stripped))


# ─────────────────────────────────────────────────────────────────────
# Section: 结果数据结构
# ─────────────────────────────────────────────────────────────────────


@dataclass
class CaseVerdict:
    """单个 case 的评测判定结果。"""

    eval_id: str = ""
    passed: bool = True
    score: float = 1.0          # 每条 invocation 通过率的均值（0~1）
    category: FailureCategory | None = None
    detail: str = ""            # 人类可读的失败原因
    evidence: str = ""          # 触发该结论的证据（期望 vs 实际摘录）
    expected_final: str = ""
    actual_final: str = ""


# ─────────────────────────────────────────────────────────────────────
# Section: 工具轨迹比较
# ─────────────────────────────────────────────────────────────────────


def _get_invocation_parts(invocation: dict) -> tuple[str, str]:
    """从一条 invocation 中提取 user_content 文本和 final_response 文本。"""
    user = ""
    final = ""
    user_content = invocation.get("user_content") or {}
    if isinstance(user_content, dict):
        for part in user_content.get("parts", []) or []:
            user += str(part.get("text", "") or "")
    final_content = invocation.get("final_response") or {}
    if isinstance(final_content, dict):
        for part in final_content.get("parts", []) or []:
            final += str(part.get("text", "") or "")
    return user, final


def _extract_tool_uses(intermediate: dict) -> list[dict]:
    """从 intermediate_data 提取 tool_uses 列表。"""
    if not intermediate:
        return []
    return intermediate.get("tool_uses", []) or []


def _tool_name(tool: dict) -> str:
    """提取 tool 名（兼容 name/function_name 两种字段）。"""
    return str(tool.get("name") or tool.get("function_name") or tool.get("tool_name") or "")


def _tool_result_text(tool: dict) -> str:
    """提取 tool 结果文本（兼容 result/output/response 字段）。"""
    return str(tool.get("result") or tool.get("output") or tool.get("response") or "")


def _compare_tools(expected_tools: list[dict], actual_tools: list[dict]) -> tuple[bool, str, str]:
    """比较期望工具轨迹与实际工具轨迹。

    返回 (是否通过, 类别, 证据)。
    仅当期望 invocation 带工具数据时启用，避免对纯文本 case 误判。
    """
    if not expected_tools:
        return True, "", "no expected tools"
    if not actual_tools:
        return False, FailureCategory.TOOL_CALL_ERROR, "expected tool call but actual has none"

    # 工具名比较（顺序对应）
    for i, exp_tool in enumerate(expected_tools):
        if i >= len(actual_tools):
            return False, FailureCategory.TOOL_CALL_ERROR, f"expected {len(expected_tools)} tools, actual has {len(actual_tools)}"
        act_tool = actual_tools[i]
        exp_name = _tool_name(exp_tool)
        act_name = _tool_name(act_tool)
        if exp_name and act_name and normalize_text(exp_name) != normalize_text(act_name):
            return False, FailureCategory.WRONG_TOOL_SELECTED, f"expected tool '{exp_name}', actual '{act_name}'"
        # 结果比较（宽松字符串）
        exp_res = normalize_text(_tool_result_text(exp_tool))
        act_res = normalize_text(_tool_result_text(act_tool))
        if exp_res and act_res and exp_res not in act_res and act_res not in exp_res:
            return False, FailureCategory.TOOL_CALL_ERROR, f"tool '{exp_name or i}' result mismatch"

    return True, "", "tools matched"


def _tool_result_vs_answer(expected_final: str, actual_final: str, actual_tools: list[dict]) -> tuple[bool, str]:
    """工具结果与最终答案一致性检查。

    若期望是裸数字、且实际最后一个工具结果与期望不一致，而最终回复又声称是该答案，
    说明工具调用结果被错误使用 → tool_call_error。
    仅当最后一个工具结果与期望不同且最终答案数字与期望不同时触发。
    """
    exp_num = bare_answer(expected_final)
    if exp_num is None or not actual_tools:
        return True, ""
    last_result = _last_number(_tool_result_text(actual_tools[-1]))
    act_num = _last_number(actual_final)
    if last_result is None:
        return True, ""
    # 工具结果与期望不符，且最终答案也不符 → 工具结果错误使用
    if abs(last_result - exp_num) > 1e-6 and (act_num is None or abs(act_num - exp_num) > 1e-6):
        return False, FailureCategory.TOOL_CALL_ERROR
    return True, ""


# ─────────────────────────────────────────────────────────────────────
# Section: 格式层检测
# ─────────────────────────────────────────────────────────────────────


def _user_demands_simple_format(user_text: str) -> str | None:
    """检测 user prompt 是否要求特定简单输出格式。

    返回 "number" / "json" / "markdown" / None。
    """
    if not user_text:
        return None
    low = str(user_text).lower()
    # 放宽匹配：ONLY the numeric result / just the number / answer with only the number 等
    if re.search(r"only.{0,15}(numeric|number)|only.{0,4}the number|just.{0,4}(the |)number|numeric.{0,6}(result|answer)", low):
        return "number"
    if re.search(r"\bjson\b", low):
        return "json"
    if re.search(r"markdown|markdown table", low):
        return "markdown"
    return None


def _check_format(user_text: str, expected_final: str, actual_final: str) -> tuple[bool, str]:
    """格式层检查：期望数字答案但实际带多余 prose → format_not_as_required。

    注意：必须要求"数字匹配 且 实际归一化含非数字字符"才判格式违规，
    避免纯数字答案因归一化差异被误判。
    """
    fmt = _user_demands_simple_format(user_text)
    if fmt is None:
        return True, ""
    exp_num = bare_answer(expected_final)
    if exp_num is None:
        # 非数字期望不适用格式层
        return True, ""
    act_num = _last_number(actual_final)
    if act_num is None or abs(act_num - exp_num) > 1e-6:
        # 数字本身不匹配 → 交给其它层（final_response_mismatch）
        return True, ""
    # 数字匹配，但实际回复含非数字字符（prose 污染）→ 格式违规
    norm_actual = normalize_text(actual_final)
    if re.search(r"[^\d.%-]", norm_actual):
        return False, FailureCategory.FORMAT_NOT_AS_REQUIRED
    return True, ""


# ─────────────────────────────────────────────────────────────────────
# Section: 单条 invocation 比较
# ─────────────────────────────────────────────────────────────────────


def compare_invocations(expected: dict, actual: dict) -> tuple[bool, str]:
    """比较一条期望 invocation 与一条实际 invocation。

    返回 (是否通过, 失败类别或空串)。
    """
    exp_user, exp_final = _get_invocation_parts(expected)
    act_user, act_final = _get_invocation_parts(actual)

    if not exp_final:
        return False, FailureCategory.MISSING_EXPECTED_OUTPUT

    # 工具层：仅当期望含工具数据时启用
    exp_inter = expected.get("intermediate_data") or {}
    act_inter = actual.get("intermediate_data") or {}
    exp_tools = _extract_tool_uses(exp_inter)
    act_tools = _extract_tool_uses(act_inter)

    tool_ok, tool_cat, tool_evidence = _compare_tools(exp_tools, act_tools)
    # 工具结果 vs 最终答案
    tv_ok, tv_cat = _tool_result_vs_answer(exp_final, act_final, act_tools)

    # 格式层
    fmt_ok, fmt_cat = _check_format(exp_user, exp_final, act_final)

    # 最终答案匹配
    exp_bare = bare_answer(exp_final)
    exp_norm = normalize_text(exp_final)
    act_norm = normalize_text(act_final)

    answer_ok = False
    answer_reason = ""
    if exp_bare is not None and _is_numeric_only(exp_final):
        # 纯数字期望：匹配实际回复中"答案位置的数字"。
        # 答案位置 = 等号后面的数字 ∪ 最后一个数字。这兼容：
        #   "144 / 12 = 13" → =后 [13] ≠ 12 → 失败（正确捕获算错）
        #   "225 / 15 = 15 because..." → =后含 15 → 通过
        #   "BMI = ... = 22.86 kg/m^2" → =后含 22.86 → 通过
        # 注意：期望"答案在开头"（如 "0.75 = 75/100"）由 Phase 2 数据归一化
        # 统一为 =后/末尾格式，这里不做过度猜测。
        act_nums = extract_numbers(act_final)
        # 支持 = $386.66、= 100、= $15,353.13（千分位逗号）等货币/百分号答案格式
        eq_nums = [
            float(m.replace(",", ""))
            for m in re.findall(r"=\s*[$€£]?\s*(-?\d+(?:,\d{3})*(?:\.\d+)?)", act_final)
        ]
        candidates = eq_nums if eq_nums else [act_nums[-1]] if act_nums else []
        if any(abs(a - exp_bare) <= 1e-6 for a in candidates):
            answer_ok = True
        else:
            answer_reason = f"expected numeric {exp_bare} not found as answer in actual"
    elif len(exp_norm) <= 20 and _unit_is_word(exp_final):
        # 期望是"数字+真实单位词"（如 "785.40 cubic cm"、"48厘米"、"10%"）：
        # 用数字比较（含舍入容差），且单位词（归一化后的非数字部分）必须出现在实际中。
        # 绝不裸用 contains，避免"48厘米"命中"48平方厘米"。
        # 单位词必须不含数字（排除 "30 and 20" → and20、"Length 10" → width5 等多数字场景）。
        exp_num = _first_number(exp_final)
        unit_part = _unit_of(exp_final)
        act_num = _last_number(act_final)
        if act_num is not None and (abs(act_num - exp_num) <= 1e-6 or _round_close(act_num, exp_num)):
            if unit_part and unit_part not in act_norm:
                answer_reason = f"expected unit '{unit_part}' not found in actual"
            else:
                answer_ok = True
        else:
            answer_reason = f"expected numeric-with-unit {exp_num}, actual ends with {act_num}"
    elif len(exp_norm) <= 20:
        # 类答案期望：归一化 contains 或期望数字子集
        # （兼容实际为长解释、答案数字散布在推导中的场景）
        if exp_norm in act_norm:
            answer_ok = True
        else:
            exp_nums = extract_numbers(exp_final)
            act_nums = extract_numbers(act_final)
            if exp_nums and all(any(abs(e - a) <= 1e-6 for a in act_nums) for e in exp_nums):
                answer_ok = True
            else:
                answer_reason = f"expected answer '{exp_final}' not matched in actual"
    else:
        # 类解释期望：contains 或期望数字子集
        if exp_norm in act_norm:
            answer_ok = True
        else:
            exp_nums = extract_numbers(exp_final)
            act_nums = extract_numbers(act_final)
            if exp_nums and all(any(abs(e - a) <= 1e-6 for a in act_nums) for e in exp_nums):
                answer_ok = True
            else:
                answer_reason = f"expected explanation not matched; expected numbers {exp_nums} not all in actual {act_nums}"

    # 汇总：类别优先级
    failures: list[tuple[int, str]] = []
    if not fmt_ok and fmt_cat:
        failures.append((_CATEGORY_PRIORITY[fmt_cat], fmt_cat))
    if not tv_ok and tv_cat:
        failures.append((_CATEGORY_PRIORITY[tv_cat], tv_cat))
    if not tool_ok and tool_cat:
        failures.append((_CATEGORY_PRIORITY[tool_cat], tool_cat))
    if not answer_ok:
        failures.append((_CATEGORY_PRIORITY[FailureCategory.FINAL_RESPONSE_MISMATCH], FailureCategory.FINAL_RESPONSE_MISMATCH))

    if failures:
        failures.sort(key=lambda x: x[0])
        return False, failures[0][1]
    return True, ""


# ─────────────────────────────────────────────────────────────────────
# Section: 单 case 比较
# ─────────────────────────────────────────────────────────────────────


def _conversation_texts(invocation: dict) -> tuple[str, str]:
    """辅助：从 invocation 提取 user/final 文本（与 _get_invocation_parts 一致）。"""
    return _get_invocation_parts(invocation)


def compare_case(case: dict) -> CaseVerdict:
    """对一个 evalset case 做完整评测，返回 CaseVerdict。"""
    eval_id = str(case.get("eval_id") or "unknown")
    conversation = case.get("conversation") or []
    actual = case.get("actual_conversation") or []

    if not conversation:
        return CaseVerdict(
            eval_id=eval_id,
            passed=False,
            score=0.0,
            category=FailureCategory.MISSING_EXPECTED_OUTPUT,
            detail="case 缺少 conversation 字段（无期望输出）",
            evidence="missing conversation",
        )

    if not actual:
        # legacy 兼容：无 actual_conversation 视为通过（无分歧证据）
        exp_user, exp_final = _conversation_texts(conversation[0]) if conversation else ("", "")
        return CaseVerdict(
            eval_id=eval_id,
            passed=True,
            score=1.0,
            expected_final=exp_final,
            actual_final="",
            detail="legacy case 无 actual_conversation，按通过处理",
        )

    # 逐 invocation 比较（取两方最小长度对齐）
    n = min(len(conversation), len(actual))
    scores: list[float] = []
    failure_info: list[tuple[int, str, str]] = []  # (priority, category, detail)

    for i in range(n):
        exp = conversation[i]
        act = actual[i]
        ok, cat = compare_invocations(exp, act)
        scores.append(1.0 if ok else 0.0)
        if not ok:
            _, exp_final = _conversation_texts(exp)
            _, act_final = _conversation_texts(act)
            detail = f"invocation {i + 1} 失败: {cat}"
            failure_info.append((_CATEGORY_PRIORITY.get(cat, 99), cat, detail))

    # 长度不一致：期望比实际多 → 有缺失输出
    if len(conversation) > len(actual):
        missing_count = len(conversation) - len(actual)
        for i in range(len(actual), len(conversation)):
            scores.append(0.0)
        failure_info.append(
            (99, FailureCategory.MISSING_EXPECTED_OUTPUT,
             f"期望 {len(conversation)} 条 invocation，实际只有 {len(actual)} 条")
        )

    if not scores:
        return CaseVerdict(eval_id=eval_id, passed=True, score=1.0)

    score = sum(scores) / len(scores)
    passed = score >= 1.0 - 1e-9

    if passed:
        _, exp_final = _conversation_texts(conversation[0])
        _, act_final = _conversation_texts(actual[0]) if actual else ("", "")
        return CaseVerdict(
            eval_id=eval_id,
            passed=True,
            score=score,
            expected_final=exp_final,
            actual_final=act_final,
        )

    # 选最高优先级类别作为根因
    failure_info.sort(key=lambda x: x[0])
    _, top_cat, top_detail = failure_info[0]

    # 组装证据
    evidence_parts = []
    first_exp = ""
    first_act = ""
    for i in range(n):
        _, exp_final = _conversation_texts(conversation[i])
        _, act_final = _conversation_texts(actual[i])
        if i == 0:
            first_exp = exp_final
            first_act = act_final
        if normalize_text(exp_final) != normalize_text(act_final):
            evidence_parts.append(f"[inv{i + 1}] 期望「{exp_final[:60]}」vs 实际「{act_final[:60]}」")
    evidence = "；".join(evidence_parts[:3]) or top_detail

    return CaseVerdict(
        eval_id=eval_id,
        passed=False,
        score=score,
        category=top_cat,
        detail=top_detail,
        evidence=evidence,
        expected_final=first_exp,
        actual_final=first_act,
    )


# ─────────────────────────────────────────────────────────────────────
# Section: TraceMatcher（可调阈值包装）
# ─────────────────────────────────────────────────────────────────────


@dataclass
class TraceMatcher:
    """trace 回放匹配器，封装可调阈值。"""

    numeric_tolerance: float = 1e-6
    contains_len_threshold: int = 20   # 期望归一化文本 ≤ 此长度按"类答案"处理

    def evaluate(self, case: dict) -> CaseVerdict:
        """对 case 做评测。"""
        return compare_case(case)

    def evaluate_batch(self, cases: list[dict]) -> list[CaseVerdict]:
        """批量评测。"""
        return [self.evaluate(c) for c in cases]


def default_matcher() -> TraceMatcher:
    """返回默认 TraceMatcher 实例。"""
    return TraceMatcher()
