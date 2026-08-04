"""Comparator 单元测试 — 覆盖分层评测规则。

验证 TraceMatcher 的：纯数字匹配、contains 匹配、带单位匹配、
格式层（ONLY-number）、工具层、类别优先级、legacy 兼容。
"""

import json
import sys
from pathlib import Path

import pytest

_parent = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_parent))

from pipeline.comparator import (
    FailureCategory,
    TraceMatcher,
    bare_answer,
    compare_case,
    compare_invocations,
    default_matcher,
    extract_numbers,
    normalize_text,
)


def _mk_case(expected_final: str, actual_final: str, *, user: str = "Test question?",
             expected_tools: list | None = None, actual_tools: list | None = None,
             eval_id: str = "case_001") -> dict:
    """构造一个单 invocation 的 evalset case。

    工具数据结构与真实 evalset 一致：
    - tool_uses: [{"tool_name": "...", "arguments": {...}}]
    - tool_responses: [{"result": "..."}]
    旧格式 {"name", "result"} 会被拆分为 uses + responses。
    """
    def _split(tools: list) -> tuple[list, list]:
        uses, responses = [], []
        for t in tools or []:
            uses.append({
                "tool_name": t.get("tool_name") or t.get("name") or "",
                "arguments": t.get("arguments") or {},
            })
            if "result" in t or "output" in t or "response" in t:
                responses.append({"result": t.get("result") or t.get("output") or t.get("response") or ""})
        return uses, responses

    exp_uses, exp_resp = _split(expected_tools)
    act_uses, act_resp = _split(actual_tools)

    conv = {
        "invocation_id": "inv-001",
        "user_content": {"parts": [{"text": user}], "role": "user"},
        "final_response": {"parts": [{"text": expected_final}], "role": "model"},
    }
    if expected_tools is not None:
        conv["intermediate_data"] = {"tool_uses": exp_uses, "tool_responses": exp_resp}
    actual = {
        "invocation_id": "inv-001",
        "user_content": {"parts": [{"text": user}], "role": "user"},
        "final_response": {"parts": [{"text": actual_final}], "role": "model"},
    }
    if actual_tools is not None:
        actual["intermediate_data"] = {"tool_uses": act_uses, "tool_responses": act_resp}
    return {
        "eval_id": eval_id,
        "eval_mode": "trace",
        "conversation": [conv],
        "actual_conversation": [actual],
    }


class TestNormalize:
    def test_nfkc_normalization(self):
        """全角字符归一化。"""
        assert normalize_text("ＡＮＤ") == "and"
        assert normalize_text("２５＋１７") == "2517"

    def test_strip_punctuation(self):
        """常见标点被剥离，中文/数字/字母保留。"""
        assert normalize_text("25 + 17 = 42") == "251742"
        assert normalize_text("你好，世界") == "你好世界"

    def test_preserve_decimal_and_negative(self):
        """小数点和负号必须保留（避免误判小数/负数）。"""
        assert normalize_text("-3.14") == "-3.14"
        assert normalize_text("0.1 + 0.2 = 0.3") == "0.10.20.3"


class TestNumberExtraction:
    def test_extract_numbers(self):
        assert extract_numbers("70 / 3.0625 = 22.86") == [70.0, 3.0625, 22.86]

    def test_bare_answer(self):
        assert bare_answer("42") == 42.0
        assert bare_answer("$26.99") == 26.99
        assert bare_answer("12%") == 12.0
        assert bare_answer("785.40 cubic cm") is None  # 带单位非裸答案
        assert bare_answer("The answer is 5") is None  # 含 prose


class TestCompareInvocations:
    def test_exact_match(self):
        """完全一致 → 通过。"""
        ok, cat = compare_invocations(
            _mk_case("42", "42")["conversation"][0],
            _mk_case("42", "42")["actual_conversation"][0],
        )
        assert ok and not cat

    def test_numeric_equal(self):
        """纯数字期望用数值相等（容忍格式差异）。"""
        ok, cat = compare_invocations(
            _mk_case("42", "25 + 17 = 42")["conversation"][0],
            _mk_case("42", "25 + 17 = 42")["actual_conversation"][0],
        )
        assert ok

    def test_numeric_mismatch(self):
        """纯数字期望实际不同 → final_response_mismatch。"""
        ok, cat = compare_invocations(
            _mk_case("12", "144 / 12 = 13")["conversation"][0],
            _mk_case("12", "144 / 12 = 13")["actual_conversation"][0],
        )
        assert not ok and cat == FailureCategory.FINAL_RESPONSE_MISMATCH

    def test_contains_match(self):
        """类答案期望走 contains（非纯数字短期望）。"""
        ok, cat = compare_invocations(
            _mk_case("3/4", "0.75 = 75/100 = 3/4")["conversation"][0],
            _mk_case("3/4", "0.75 = 75/100 = 3/4")["actual_conversation"][0],
        )
        assert ok

    def test_contains_mismatch(self):
        """类答案期望未命中 → 失败。"""
        ok, cat = compare_invocations(
            _mk_case("7/8", "0.75 = 75/100 = 3/4")["conversation"][0],
            _mk_case("7/8", "0.75 = 75/100 = 3/4")["actual_conversation"][0],
        )
        assert not ok
        assert cat == FailureCategory.FINAL_RESPONSE_MISMATCH

    def test_numeric_answer_in_middle(self):
        """纯数字答案出现在 = 后。"""
        ok, cat = compare_invocations(
            _mk_case("15", "225 / 15 = 15 because 15 * 15 = 225")["conversation"][0],
            _mk_case("15", "225 / 15 = 15 because 15 * 15 = 225")["actual_conversation"][0],
        )
        assert ok

    def test_numeric_negation_context_not_matched(self):
        """否定语境（= 15 is wrong, real = 14）中的中间值不得误判为答案。"""
        ok, cat = compare_invocations(
            _mk_case("15", "answer = 15 is wrong, real answer = 14")["conversation"][0],
            _mk_case("15", "answer = 15 is wrong, real answer = 14")["actual_conversation"][0],
        )
        assert not ok

    def test_unit_word_match(self):
        """带单位期望：数字匹配 + 单位词存在于实际。"""
        ok, cat = compare_invocations(
            _mk_case("785.40 cubic cm", "V = 785.3975 cubic cm")["conversation"][0],
            _mk_case("785.40 cubic cm", "V = 785.3975 cubic cm")["actual_conversation"][0],
        )
        assert ok  # 舍入容差

    def test_unit_word_mismatch(self):
        """带单位期望：单位词不在实际 → 失败。"""
        ok, cat = compare_invocations(
            _mk_case("48平方米", "48平方厘米")["conversation"][0],
            _mk_case("48平方米", "48平方厘米")["actual_conversation"][0],
        )
        assert not ok
        assert cat == FailureCategory.FINAL_RESPONSE_MISMATCH

    def test_unit_word_substring_mismatch(self):
        """单位词互为子串时不得误通过（锁定"绝不裸用 contains"语义）。

        期望单位必须是实际中的独立单位词：厘米⊂平方厘米、米⊂厘米、
        m⊂cm 都不算单位匹配。
        """
        for exp, act in (("48厘米", "48平方厘米"), ("48米", "48厘米"), ("48m", "48cm")):
            ok, cat = compare_invocations(
                _mk_case(exp, act)["conversation"][0],
                _mk_case(exp, act)["actual_conversation"][0],
            )
            assert not ok, f"期望 {exp} / 实际 {act} 不应误通过"
            assert cat == FailureCategory.FINAL_RESPONSE_MISMATCH

    def test_unit_word_exact_match_still_passes(self):
        """单位词完全相等（含派生单位自身）仍通过。"""
        for exp, act in (("48厘米", "48厘米"), ("48平方米", "48平方米"), ("48cm", "48 cm")):
            ok, cat = compare_invocations(
                _mk_case(exp, act)["conversation"][0],
                _mk_case(exp, act)["actual_conversation"][0],
            )
            assert ok, f"期望 {exp} / 实际 {act} 应通过"

    def test_negation_beyond_short_window(self):
        """否定词在 40~80 字符窗口（原 40 窗口会漏判）：被否定的中间值不得
        误判为通过（reviewer Warning ③）。"""
        case = _mk_case(
            "15",
            "= 15, I double-checked carefully and found the earlier value incorrect",
        )
        ok, cat = compare_invocations(case["conversation"][0], case["actual_conversation"][0])
        assert not ok
        assert cat == FailureCategory.FINAL_RESPONSE_MISMATCH

    def test_negation_beyond_80_char_window(self):
        """否定词在 >80 字符（原固定 80 窗口之外）：仍须捕获，不误判通过。

        否定窗口改为"到下一个 = 候选为止"的语境段，远距离否定也能命中
        （reviewer Warning ④）。同时验证后续独立候选不受影响。"""
        # 否定词距候选 >80 字符（填充 100 个 x），且候选仍应被排除
        far = "= 15, " + "x" * 100 + " the earlier value is incorrect"
        case = _mk_case("15", far)
        ok, cat = compare_invocations(case["conversation"][0], case["actual_conversation"][0])
        assert not ok
        assert cat == FailureCategory.FINAL_RESPONSE_MISMATCH

    def test_negation_segment_does_not_affect_next_candidate(self):
        """被否定的 `= 15` 不吞掉后续独立候选 `= 14`：14 仍应被正确匹配。"""
        case = _mk_case(
            "14",
            "= 15 is wrong (earlier miscalculation), the correct value is = 14",
        )
        ok, cat = compare_invocations(case["conversation"][0], case["actual_conversation"][0])
        assert ok

    def test_negation_segment_ignores_comparison_equals(self):
        """`>=`/`==` 中的等号不作候选切段边界：否定词在其后仍须命中。

        裸 find("=") 会把比较表达式里的 = 当"下一个候选起点"，否定语境段被
        截短而漏判否定（reviewer Warning）。"""
        for act in ("= 15, 10 >= 15 wrong", "= 15, 15 == 15 incorrect"):
            case = _mk_case("15", act)
            ok, cat = compare_invocations(
                case["conversation"][0], case["actual_conversation"][0])
            assert not ok, f"期望 15 不应匹配被否定的实际: {act}"
            assert cat == FailureCategory.FINAL_RESPONSE_MISMATCH

    def test_comparison_operator_not_an_answer_equals(self):
        """`==`/`>=` 比较表达式中的 = 不作答案候选（reviewer Warning）。

        `x == 42, answer = 10` 中 42 是比较操作数而非答案：期望 42 不得
        误判通过；只有独立赋值等号 `= 10` 后的 10 是候选。修前 eq_candidates
        会把 `== 42`/`>= 42` 的 = 当答案等号 → 候选含 42 → 误判通过。
        """
        for act in ("x == 42, answer = 10", "10 >= 42, answer = 10"):
            case = _mk_case("42", act)
            ok, cat = compare_invocations(
                case["conversation"][0], case["actual_conversation"][0])
            assert not ok, f"期望 42 不应匹配比较操作数: {act}"
            assert cat == FailureCategory.FINAL_RESPONSE_MISMATCH

    def test_multi_numeric_expected_requires_one_to_one(self):
        """多数字期望须一一对应：`20 and 20` 不得命中实际单个 `20`。

        旧实现 `all(any(...))` 允许两个期望 20 折叠到同一实际数字 → 误判
        通过（reviewer Warning）。
        """
        case = _mk_case("20 and 20", "answer is 20")
        ok, cat = compare_invocations(
            case["conversation"][0], case["actual_conversation"][0])
        assert not ok, "期望 20 and 20 不应命中单个实际 20"
        assert cat == FailureCategory.FINAL_RESPONSE_MISMATCH

        # 两个实际数字 → 一一对应通过
        case2 = _mk_case("20 and 20", "answer is 20 and 20")
        ok2, _ = compare_invocations(
            case2["conversation"][0], case2["actual_conversation"][0])
        assert ok2

    def test_trace_matcher_numeric_tolerance_effective(self):
        """TraceMatcher 自定义 numeric_tolerance 真正生效，非静默忽略
        （reviewer Warning：硬编码阈值被忽略的问题）。"""
        case = _mk_case("42", "answer is 42.001")
        # 默认 1e-6：42 vs 42.001 差 0.001 → 不匹配
        ok, _ = compare_invocations(case["conversation"][0], case["actual_conversation"][0])
        assert not ok
        # 自定义 0.01：42 vs 42.001 差 0.001 → 匹配
        m = TraceMatcher(numeric_tolerance=0.01)
        v = m.evaluate(case)
        assert v.passed

    def test_format_number_only(self):
        """ONLY-number 但实际带 prose → format_not_as_required。"""
        case = _mk_case(
            "391", "The product is 391.",
            user="Calculate the product of 17 and 23. Reply with ONLY the numeric result, nothing else.",
        )
        ok, cat = compare_invocations(case["conversation"][0], case["actual_conversation"][0])
        assert not ok and cat == FailureCategory.FORMAT_NOT_AS_REQUIRED

    def test_format_number_only_ok(self):
        """ONLY-number 且实际纯数字 → 通过。"""
        case = _mk_case(
            "391", "391",
            user="Calculate the product of 17 and 23. Reply with ONLY the numeric result, nothing else.",
        )
        ok, cat = compare_invocations(case["conversation"][0], case["actual_conversation"][0])
        assert ok

    def test_tool_result_vs_answer(self):
        """工具结果与期望不符且最终答案不符 → tool_call_error。"""
        expected_tools = [{"name": "calc", "result": "15721.25"}]
        actual_tools = [{"name": "calc", "result": "15353.13"}]
        case = _mk_case(
            "$15721.25", "The result is $15353.13",
            expected_tools=expected_tools, actual_tools=actual_tools,
        )
        ok, cat = compare_invocations(case["conversation"][0], case["actual_conversation"][0])
        assert not ok and cat == FailureCategory.TOOL_CALL_ERROR

    def test_wrong_tool_selected(self):
        """工具名不同 → wrong_tool_selected。"""
        expected_tools = [{"name": "multiply", "result": "12"}]
        actual_tools = [{"name": "add", "result": "12"}]
        case = _mk_case(
            "12", "12",
            expected_tools=expected_tools, actual_tools=actual_tools,
        )
        ok, cat = compare_invocations(case["conversation"][0], case["actual_conversation"][0])
        assert not ok and cat == FailureCategory.WRONG_TOOL_SELECTED


class TestCompareCase:
    def test_missing_expected(self):
        """无 conversation → missing_expected_output。"""
        v = compare_case({"eval_id": "x", "conversation": [], "actual_conversation": []})
        assert not v.passed and v.category == FailureCategory.MISSING_EXPECTED_OUTPUT

    def test_legacy_no_actual(self):
        """无 actual_conversation → 通过（legacy 兼容），但标记 unreviewed。"""
        case = {
            "eval_id": "x",
            "conversation": [{"final_response": {"parts": [{"text": "42"}]}}],
        }
        v = compare_case(case)
        assert v.passed
        assert v.unreviewed is True
        assert v.detail.startswith("未评测")

    def test_actual_present_not_unreviewed(self):
        """有 actual_conversation 且通过 → 不算未评测，计入 pass_rate。"""
        case = {
            "eval_id": "x",
            "conversation": [{"final_response": {"parts": [{"text": "42"}]}}],
            "actual_conversation": [{"final_response": {"parts": [{"text": "42"}]}}],
        }
        v = compare_case(case)
        assert v.passed
        assert v.unreviewed is False

    def test_multiple_invocations_score(self):
        """多 invocation 时 score 为均值。"""
        case = {
            "eval_id": "multi",
            "conversation": [
                {"user_content": {"parts": [{"text": "q1"}]},
                 "final_response": {"parts": [{"text": "42"}]}},
                {"user_content": {"parts": [{"text": "q2"}]},
                 "final_response": {"parts": [{"text": "7"}]}},
            ],
            "actual_conversation": [
                {"user_content": {"parts": [{"text": "q1"}]},
                 "final_response": {"parts": [{"text": "42"}]}},
                {"user_content": {"parts": [{"text": "q2"}]},
                 "final_response": {"parts": [{"text": "8"}]}},
            ],
        }
        v = compare_case(case)
        assert not v.passed
        assert v.score == pytest.approx(0.5, abs=1e-6)


class TestTraceMatcher:
    def test_default_matcher(self):
        m = default_matcher()
        assert isinstance(m, TraceMatcher)

    def test_evaluate_batch(self):
        m = default_matcher()
        cases = [_mk_case("42", "42"), _mk_case("42", "43")]
        verdicts = m.evaluate_batch(cases)
        assert [v.passed for v in verdicts] == [True, False]

    def test_real_evalset_train_failures(self, data_dir):
        """真实 train evalset：所有 _fail 标注 case 应失败。"""
        path = data_dir / "train.evalset.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        m = default_matcher()
        fail_ids = []
        for c in data["eval_cases"]:
            if "_fail" in c.get("eval_id", ""):
                v = m.evaluate(c)
                if not v.passed:
                    fail_ids.append(c["eval_id"])
        # 至少 7/10 个 _fail case 被识别（剩余 2 个数据标注错误 + 1 个格式 case
        # 在 Phase 2 数据归一化中处理，届时收紧到 10/10）
        assert len(fail_ids) >= 7
