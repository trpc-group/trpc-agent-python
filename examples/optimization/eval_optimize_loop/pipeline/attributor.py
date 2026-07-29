# -*- coding: utf-8 -*-
# Copyright @ 2025 Tencent.com
"""Stage 2: Failure attribution — classify and cluster evaluation failures."""

from __future__ import annotations

import difflib
import json
import re

from .config import BaselineResult, CaseResult, FailureRecord, FailureReport


FAILURE_TYPE = {
    "MISSING_INFORMATION": "missing_information",
    "EXCESSIVE_VERBOSITY": "excessive_verbosity",
    "REASONING_FAILURE": "reasoning_failure",
    "TOOL_CALL_ERROR": "tool_call_error",
    "TOOL_ARG_ERROR": "tool_arg_error",
    "OVERGENERALIZATION": "overgeneralization",
    "NO_RESPONSE": "no_response",
    "FORMAT_ERROR": "format_error",
    "CONTRADICTORY_INFORMATION": "contradictory_information",
    "HALLUCINATION": "hallucination",
    "TONE_STYLE_MISMATCH": "tone_style_mismatch",
}

# ── Heuristic signals (tuned to reduce false "excessive_verbosity") ──
# Only treat as verbosity if the response is SUBSTANTIALLY longer than expected
# AND contains clear verbose markers.
_STRONG_VERBOSITY_MARKERS = [
    "用户问了", "首先，", "其次，", "最后，", "另外，", "补充一下",
    "Let me", "Let's", "I'm going to",
    "Here's a breakdown", "In summary", "To summarize",
    "As a shopping assistant", "作为", "以下是",
    "🎉", "🍌", "🍎", "💰", "📦", "🚚", "👍", "✅", "⭐",
    "###", "**优点", "**缺点",
]

# Markers that indicate FORMAT issues in the response
_FORMAT_ERROR_MARKERS = [
    "```json", "```", "<think>", "</think>", "<tool>", "</tool>",
    "Traceback", "Error:", "Exception:", "NoneType",
    "{", "}", "[", "]",  # JSON-ish but used incorrectly → check separately
]

# Markers that suggest hallucination / fabrication
_HALLUCINATION_MARKERS = [
    "抱歉，我需要", "没有找到相关", "可能需要", "请您确认",
    "根据我的知识", "据我所知",
]

# Tokens that usually mean the answer is MISSING key numerical / factual content
_KEY_INFO_TOKENS = re.compile(
    r"(价格|库存|数量|金额|优惠|折扣|规格|型号|日期|时间|\d+元|\d+件|\d+\.\d+|\d+)"
)

# ── Direct rubric id / judge reason → failure type mapping ──────────
# The llm_rubric_response metric in the live-mode test_config.json defines rubric ids:
#   "completeness", "no_redundancy", "no_extra"
# These are the MOST RELIABLE signals — if the judge explicitly marks a rubric as
# FAILED we take that as ground truth even when the actual_response text heuristics
# would otherwise misclassify (e.g. deepseek "too smart" to look like it's
# missing info, but the judge still said it violated no_extra → overgeneralization).
# Pattern: rubric id or keyword in the judge's reason string.
RUBRIC_TO_FAILURE = [
    # (keyword_matches_reason, rubric_id, failure_type, explanation_suffix)
    (
        ("no_redundancy", "冗余", "重复", "重复出现", "多次提到", "多次出现"),
        "no_redundancy",
        FAILURE_TYPE["EXCESSIVE_VERBOSITY"],
        "违反 no_redundancy：同一信息重复表达",
    ),
    (
        ("no_extra", "额外", "扩展", "推荐", "营销", "emoji", "表情", "多余的", "没问的",
         "未询问", "用户没有问", "未要求的信息"),
        "no_extra",
        FAILURE_TYPE["OVERGENERALIZATION"],
        "违反 no_extra：输出了用户未询问的额外信息（推荐/扩展/营销话术/emoji）",
    ),
    (
        ("completeness", "不完整", "遗漏", "没覆盖", "没有回答", "未回答", "没回应", "未覆盖"),
        "completeness",
        FAILURE_TYPE["MISSING_INFORMATION"],
        "违反 completeness：未覆盖用户所有子问题",
    ),
]


def _classify_by_rubric_reason(
    metric_reason: str,
    rubric_scores: list | None = None,
) -> list[tuple[str, str]]:
    """Map failed rubric items to failure types.

    Structured per-rubric scores are authoritative.  The SDK's aggregate
    ``reason`` concatenates explanations for both passed and failed rubrics, so
    treating the mere presence of a rubric id as failure creates false
    positives.  Text parsing is retained only for older/unstructured results.
    """
    results: list[tuple[str, str]] = []
    rid_to_ftype = {
        "no_redundancy": FAILURE_TYPE["EXCESSIVE_VERBOSITY"],
        "no_extra": FAILURE_TYPE["OVERGENERALIZATION"],
        "completeness": FAILURE_TYPE["MISSING_INFORMATION"],
    }
    rid_to_msg = {
        "no_redundancy": "judge rubric=no_redundancy 未通过：存在重复表达",
        "no_extra": "judge rubric=no_extra 未通过：输出用户未询问的额外信息（过度泛化）",
        "completeness": "judge rubric=completeness 未通过：遗漏子问题/信息不完整",
    }

    known_structured_scores = []
    for item in rubric_scores or []:
        if isinstance(item, dict):
            rubric_id = str(item.get("id", ""))
            score = item.get("score", 0.0)
            item_reason = str(item.get("reason", "") or "")
        else:
            rubric_id = str(getattr(item, "id", ""))
            score = getattr(item, "score", 0.0)
            item_reason = str(getattr(item, "reason", "") or "")
        if rubric_id not in rid_to_ftype:
            continue
        known_structured_scores.append(rubric_id)
        if float(score) >= 1.0:
            continue
        message = rid_to_msg[rubric_id]
        if item_reason:
            message += f"（{item_reason}）"
        results.append((rid_to_ftype[rubric_id], message))

    if known_structured_scores:
        return results
    if not metric_reason:
        return results

    rr = metric_reason
    rl = rr.lower()

    # Older results may only contain text such as
    # "Rubric no_redundancy: FAILED".  Parse those as a compatibility fallback.
    try:
        explicit_rubric_hits = re.findall(
            r"(rubric|rule|项目|维度)?\s*[:：]?\s*"
            r"(no_redundancy|no_extra|completeness)"
            r"(?=[^\n;]*(?:failed|未通过|score\s*0))",
            rl,
        )
        explicit_rubric_hits = [match[-1] for match in explicit_rubric_hits]
    except Exception:
        explicit_rubric_hits = []

    seen_types: set[str] = set()
    for rid in explicit_rubric_hits:
        ftype = rid_to_ftype.get(rid)
        if ftype and ftype not in seen_types:
            results.append((ftype, rid_to_msg[rid]))
            seen_types.add(ftype)

    # 2) Fallback keyword matching (when rubric ids aren't present)
    for kws_rubric, rubric_name, ftype, desc in RUBRIC_TO_FAILURE:
        if ftype in seen_types:
            continue
        for kw in kws_rubric:
            if kw in rr or kw in rl:
                results.append((ftype, desc))
                seen_types.add(ftype)
                break

    return results


def _length_ratio(actual: str, expected: str) -> float:
    """How much longer actual is than expected. <1 means shorter."""
    if not expected:
        return len(actual) / 20.0 if actual else 0.0
    return len(actual) / max(1, len(expected))


def _response_similarity(actual: str, expected: str) -> float:
    """Quick similarity score 0–1 using character-level difflib."""
    if not actual or not expected:
        return 0.0
    return difflib.SequenceMatcher(None, actual, expected).ratio()


def _count_expected_tokens_in_actual(actual: str, expected: str) -> tuple[int, int]:
    """Count how many expected key tokens (numbers/keywords) appear in actual.

    Returns (found_count, total_expected_tokens).
    """
    if not expected:
        return 0, 0
    expected_tokens = set(re.findall(r"(\w+|\d+\.?\d*)", expected.lower()))
    # Filter too-short tokens
    expected_tokens = {t for t in expected_tokens if len(t) >= 2}
    if not expected_tokens:
        return 0, 0
    actual_lower = actual.lower()
    found = sum(1 for t in expected_tokens if t in actual_lower)
    return found, len(expected_tokens)


def _looks_like_format_error(actual: str, expected: str) -> bool:
    """Heuristic: malformed structure, JSON dumps, code blocks, error traces."""
    for marker in ("Traceback", "Exception", "NoneType", "<think>"):
        if marker in actual:
            return True
    # If expected has no markdown/code markers but actual has fenced code
    if "```" in actual and "```" not in expected:
        return True
    # If actual is pure JSON but expected is natural language
    try:
        json.loads(actual.strip())
        if not expected.strip().startswith("{"):
            return True
    except Exception:
        pass
    return False


def _looks_like_hallucination(actual: str, expected: str) -> bool:
    """Heuristic: contains disclaimer phrases not in expected, or unrelated info."""
    for hm in _HALLUCINATION_MARKERS:
        if hm in actual and hm not in expected:
            return True
    # If expected mentions specific numbers but actual doesn't contain ANY of them
    exp_numbers = set(re.findall(r"\d+\.?\d*", expected))
    act_numbers = set(re.findall(r"\d+\.?\d*", actual))
    if exp_numbers and not (exp_numbers & act_numbers):
        # Actual has zero expected numbers → likely hallucinated
        # (unless it says "no stock" or similar)
        if not any(w in actual for w in ("没有", "无", "售罄", "暂无", "none", "N/A")):
            return True
    return False


def _looks_like_contradiction(actual: str, expected: str) -> bool:
    """Heuristic: actual contains opposite / negating versions of expected tokens."""
    # Simple polarity: "有库存" vs "无库存", "大于X" vs "小于X" etc.
    neg_pairs = [
        ("有库存", "无库存"),
        ("有货", "没货"),
        ("在库", "缺货"),
        ("便宜", "贵"),
        ("高", "低"),
        ("多", "少"),
        ("支持", "不支持"),
        ("可以", "不可以"),
        ("available", "unavailable"),
        ("in stock", "out of stock"),
    ]
    actual_lower = actual.lower()
    expected_lower = expected.lower()
    for pos, neg in neg_pairs:
        if pos in expected_lower and neg in actual_lower:
            return True
        if neg in expected_lower and pos in actual_lower:
            return True
    return False


def _all_tools_called(case: CaseResult) -> bool:
    m = case.metrics.get("tool_trajectory_avg_score")
    if m:
        return m.eval_status == "PASSED"
    # No tool_trajectory metric → check actual vs expected tools match
    if not case.expected_tool_calls:
        return True
    if not case.actual_tool_calls:
        return False
    an = {c.get("name", "") for c in case.actual_tool_calls}
    en = {c.get("name", "") for c in case.expected_tool_calls}
    return en.issubset(an)


def _same_tool_multi_call(expected: list[dict]) -> bool:
    names = [c.get("name", "") for c in expected]
    return len(names) >= 2 and len(set(names)) == 1


def _classify_response_failure(
    case: CaseResult,
    failure_types: list[str],
    explanations: list[str],
    tools_ok: bool,
    metric_name: str,
    score: float,
    threshold: float,
    metric_reason: str,
) -> None:
    """Classify a response-level failure with rich heuristics.

    This helper replaces the old "if has verbosity signal → excessive_verbosity else
    missing_information" dichotomy so that format_error, reasoning_failure,
    hallucination, contradiction, and tone/style issues are correctly surfaced.
    """
    actual = case.actual_response or ""
    expected = case.expected_response or ""

    # --- 1. No response ---
    if not actual.strip():
        failure_types.append(FAILURE_TYPE["NO_RESPONSE"])
        explanations.append(f"[{metric_name}] 未生成任何回复 (score={score:.2f})")
        return

    # --- 2. Format error (malformed / error traces / code dumps) ---
    if _looks_like_format_error(actual, expected):
        failure_types.append(FAILURE_TYPE["FORMAT_ERROR"])
        reasons = []
        if "Traceback" in actual or "Error:" in actual:
            reasons.append("回复包含程序错误堆栈")
        if "```" in actual:
            reasons.append("回复包含代码块标记")
        if "<think>" in actual:
            reasons.append("回复暴露了思维链内部标签")
        try:
            json.loads(actual.strip())
            reasons.append("回复为纯 JSON 但期望自然语言")
        except Exception:
            pass
        explanations.append(
            f"[{metric_name}] 格式错误: " + "；".join(reasons or ["结构异常"])
            + f" (score={score:.2f})"
        )
        return

    # --- 3. Contradiction (opposite meaning vs expected) ---
    if _looks_like_contradiction(actual, expected):
        failure_types.append(FAILURE_TYPE["CONTRADICTORY_INFORMATION"])
        explanations.append(
            f"[{metric_name}] 信息矛盾：实际回复与期望内容含义相反"
            f" (期望: '{expected[:60]}' / 实际: '{actual[:80]}', score={score:.2f})"
        )

    # --- 4. Hallucination ---
    if _looks_like_hallucination(actual, expected):
        failure_types.append(FAILURE_TYPE["HALLUCINATION"])
        exp_numbers = set(re.findall(r"\d+\.?\d*", expected))
        if exp_numbers:
            explanations.append(
                f"[{metric_name}] 可能幻觉：期望中数字 {sorted(exp_numbers)} "
                f"在实际回复中完全缺失 (score={score:.2f})"
            )
        else:
            explanations.append(
                f"[{metric_name}] 可能幻觉：回复含免责声明或编造内容"
                f" (score={score:.2f})"
            )

    # --- 5. Excessive verbosity (strict check, avoid false positives) ---
    length_ratio = _length_ratio(actual, expected)
    sim = _response_similarity(actual, expected)
    strong_verbose_hits = [
        m for m in _STRONG_VERBOSITY_MARKERS if m in actual
    ]
    # Only call verbosity when:
    #   a) response is clearly too long (>=3x or >120 chars longer than expected), AND
    #   b) contains strong verbose markers, AND
    #   c) expected content tokens are still MOSTLY present (not missing info)
    found, total = _count_expected_tokens_in_actual(actual, expected)
    coverage = found / total if total else 1.0
    verbosity_len_ok = (
        length_ratio >= 3.0
        or (len(actual) - len(expected)) >= 120
    )
    if (
        verbosity_len_ok
        and len(strong_verbose_hits) >= 1
        and coverage >= 0.6
        and sim >= 0.25
    ):
        failure_types.append(FAILURE_TYPE["EXCESSIVE_VERBOSITY"])
        explanations.append(
            f"[{metric_name}] 回复冗余：长度 {len(actual)} / 期望 {len(expected)} "
            f"(ratio={length_ratio:.1f}x)；包含冗余信号 {strong_verbose_hits[:3]}；"
            f"关键信息覆盖度 {coverage:.0%}；内容相似度 {sim:.2f}。"
            f" score={score:.2f}"
        )
        return  # If we're SURE it's verbosity, don't also tag missing info

    # --- 6. Reasoning failure (multi-hop / aggregation needed) ---
    needs_reasoning = (
        _same_tool_multi_call(case.expected_tool_calls)
        or len(re.findall(r"\d+\.?\d*", expected)) >= 2
        or any(w in expected for w in ("总共", "合计", "总计", "总和", "平均", "比较", "哪个更", "差值"))
    )
    key_info_missing_in_actual = bool(_KEY_INFO_TOKENS.search(expected)) and not bool(
        _KEY_INFO_TOKENS.search(actual)
    )
    if needs_reasoning and tools_ok:
        # Key info tokens exist in expected but are wrong/missing in actual
        exp_numbers = set(re.findall(r"\d+\.?\d*", expected))
        act_numbers = set(re.findall(r"\d+\.?\d*", actual))
        shared_nums = exp_numbers & act_numbers
        if exp_numbers and len(shared_nums) < len(exp_numbers):
            failure_types.append(FAILURE_TYPE["REASONING_FAILURE"])
            explanations.append(
                f"[{metric_name}] 推理失败：需要跨值计算/比较"
                f" (期望数字 {sorted(exp_numbers)}，实际只出现 {sorted(shared_nums)})，"
                f"score={score:.2f}"
            )
            return

    # --- 7. Tool call chain led to missing information ---
    if not tools_ok:
        failure_types.append(FAILURE_TYPE["MISSING_INFORMATION"])
        explanations.append(
            f"[{metric_name}] 工具调用不正确导致信息缺失"
            f" (score={score:.2f})"
        )
        return

    # --- 8. Catch-all: missing information (with evidence) ---
    if found < total and coverage < 0.7:
        failure_types.append(FAILURE_TYPE["MISSING_INFORMATION"])
        explanations.append(
            f"[{metric_name}] 信息遗漏：期望 {total} 个关键词/数字，"
            f"实际仅命中 {found} 个 (覆盖度 {coverage:.0%})。"
            f"期望关键信息未出现在回复中。score={score:.2f}"
        )
    elif sim < 0.3 and len(actual) <= len(expected):
        failure_types.append(FAILURE_TYPE["MISSING_INFORMATION"])
        explanations.append(
            f"[{metric_name}] 内容偏离：与期望内容相似度仅 {sim:.2f}，"
            f"且实际更短 ({len(actual)} vs {len(expected)})，score={score:.2f}"
        )
    else:
        # Generic tone/style mismatch when everything present but still low score
        if metric_reason and any(
            kw in metric_reason for kw in ("语气", "风格", "礼貌", "客气", "concise", "verbose", "tone", "style")
        ):
            failure_types.append(FAILURE_TYPE["TONE_STYLE_MISMATCH"])
            explanations.append(
                f"[{metric_name}] 语气/风格不符：{metric_reason} (score={score:.2f})"
            )
        else:
            failure_types.append(FAILURE_TYPE["MISSING_INFORMATION"])
            extra = f"原因: {metric_reason}；" if metric_reason else ""
            explanations.append(
                f"[{metric_name}] 信息遗漏/不准确：{extra}"
                f"期望 '{expected[:80]}' vs 实际 '{actual[:80]}' "
                f"(sim={sim:.2f}, coverage={coverage:.0%}, score={score:.2f})"
            )


def _classify_case(case: CaseResult, eval_set_id: str) -> FailureRecord | None:
    if case.overall_status == "PASSED":
        return None

    failure_types: list[str] = []
    explanations: list[str] = []
    scores: dict[str, float] = {}
    tools_ok = _all_tools_called(case)

    for name, metric in case.metrics.items():
        scores[name] = metric.score
        if metric.eval_status != "FAILED":
            continue

        score_str = f"{metric.score:.2f}"
        threshold_str = f"{metric.threshold:.2f}"
        reason = getattr(metric, "reason", "") or ""

        # ── Tool trajectory failures ─────────────────────────────
        if name == "tool_trajectory_avg_score":
            if not case.actual_tool_calls and case.expected_tool_calls:
                failure_types.append(FAILURE_TYPE["TOOL_CALL_ERROR"])
                explanations.append(
                    f"[{name}] 未调用任何工具，期望 "
                    f"{[c.get('name', '?') for c in case.expected_tool_calls]}"
                    f" (score={score_str})"
                )
            elif case.actual_tool_calls and case.expected_tool_calls:
                an = {c.get("name", "") for c in case.actual_tool_calls}
                en = {c.get("name", "") for c in case.expected_tool_calls}
                if not an & en:
                    failure_types.append(FAILURE_TYPE["TOOL_CALL_ERROR"])
                    explanations.append(
                        f"[{name}] 工具完全错配：调用了 {sorted(an)}，"
                        f"期望 {sorted(en)} (score={score_str})"
                    )
                elif en.issubset(an) and len(an) > len(en):
                    failure_types.append(FAILURE_TYPE["OVERGENERALIZATION"])
                    extra = sorted(an - en)
                    explanations.append(
                        f"[{name}] 过度泛化：在不需要的场景额外调用了 {extra}，"
                        f"期望 {sorted(en)} (score={score_str})"
                    )
                elif an.issubset(en) and len(an) < len(en):
                    failure_types.append(FAILURE_TYPE["TOOL_CALL_ERROR"])
                    missing = sorted(en - an)
                    explanations.append(
                        f"[{name}] 工具调用缺失：未调用 {missing}，"
                        f"期望 {sorted(en)} (score={score_str})"
                    )
                else:
                    failure_types.append(FAILURE_TYPE["TOOL_ARG_ERROR"])
                    explanations.append(
                        f"[{name}] 工具参数错误：期望参数 "
                        f"{case.expected_tool_calls}, 实际 {case.actual_tool_calls}"
                        f" (score={score_str})"
                    )
            else:
                failure_types.append(FAILURE_TYPE["TOOL_CALL_ERROR"])
                explanations.append(
                    f"[{name}] 工具轨迹异常 (score={score_str})"
                )

        # ── Response-level failures ─────────────────────────────
        elif name in ("final_response_avg_score", "llm_rubric_response"):
            # A) FIRST: use the judge's rubric reason as GROUND TRUTH (most reliable)
            rubric_hits = _classify_by_rubric_reason(
                reason,
                metric.rubric_scores,
            )
            for ftype, msg in rubric_hits:
                if ftype not in failure_types:
                    failure_types.append(ftype)
                    explanations.append(f"[{name}] {msg} (score={metric.score:.2f})")

            # B) THEN: Heuristic classification for failure types that rubric ids
            #    do NOT directly model (hallucination / format_error /
            #    contradiction / reasoning_failure / tone mismatch).
            #    To avoid the "all missing_information" collapse we collect
            #    heuristic types into a temporary list, filter out redundant
            #    "missing_information" tag when more specific types exist, and
            #    merge into the outer lists only if not already present.
            heuristic_types: list[str] = []
            heuristic_msgs: list[str] = []
            _classify_response_failure(
                case,
                heuristic_types,
                heuristic_msgs,
                tools_ok,
                metric_name=name,
                score=metric.score,
                threshold=metric.threshold,
                metric_reason=reason,
            )
            # Remove generic "missing_information" fallback if we have ANY
            # more specific failure type from heuristic or from rubric.
            specific_types = [t for t in heuristic_types if t != "missing_information"]
            rubric_has_specific = any(
                ft != FAILURE_TYPE["MISSING_INFORMATION"]
                for ft, _ in rubric_hits
            )
            if specific_types or rubric_has_specific:
                while "missing_information" in heuristic_types:
                    idx = heuristic_types.index("missing_information")
                    heuristic_types.pop(idx)
                    if 0 <= idx < len(heuristic_msgs):
                        heuristic_msgs.pop(idx)
            # Merge into outer failure_types/explanations (no duplicates)
            for ft, msg in zip(heuristic_types, heuristic_msgs):
                if ft not in failure_types:
                    failure_types.append(ft)
                    explanations.append(msg)
            # If we still have no failure type on this metric, fall back to
            # missing_information so the case isn't silently lost.
            any_rubric_or_heuristic_for_metric = any(
                ft in failure_types for ft in (
                    FAILURE_TYPE["MISSING_INFORMATION"],
                    FAILURE_TYPE["EXCESSIVE_VERBOSITY"],
                    FAILURE_TYPE["OVERGENERALIZATION"],
                    FAILURE_TYPE["FORMAT_ERROR"],
                    FAILURE_TYPE["CONTRADICTORY_INFORMATION"],
                    FAILURE_TYPE["HALLUCINATION"],
                    FAILURE_TYPE["REASONING_FAILURE"],
                    FAILURE_TYPE["NO_RESPONSE"],
                    FAILURE_TYPE["TONE_STYLE_MISMATCH"],
                )
            )
            if not any_rubric_or_heuristic_for_metric:
                failure_types.append(FAILURE_TYPE["MISSING_INFORMATION"])
                explanations.append(
                    f"[{name}] 回复质量未达标（score={metric.score:.2f}）"
                    + (f" — {reason}" if reason else "")
                )

        # ── All other metrics → default to FORMAT_ERROR / custom ─
        else:
            if "format" in name.lower() or "schema" in name.lower() or "json" in name.lower():
                failure_types.append(FAILURE_TYPE["FORMAT_ERROR"])
                explanations.append(
                    f"[{name}] 格式/结构指标未通过: score={score_str} < {threshold_str}"
                    + (f" 原因: {reason}" if reason else "")
                )
            else:
                # Generic — use reason keywords to decide
                rl = reason.lower()
                if any(w in rl for w in ("format", "json", "schema", "结构", "格式")):
                    failure_types.append(FAILURE_TYPE["FORMAT_ERROR"])
                elif any(w in rl for w in ("tool", "arg", "param", "工具", "参数")):
                    failure_types.append(FAILURE_TYPE["TOOL_ARG_ERROR"])
                elif any(w in rl for w in ("verbose", "long", "冗余", "太长")):
                    failure_types.append(FAILURE_TYPE["EXCESSIVE_VERBOSITY"])
                elif any(w in rl for w in ("reason", "logic", "推理", "逻辑")):
                    failure_types.append(FAILURE_TYPE["REASONING_FAILURE"])
                else:
                    failure_types.append(FAILURE_TYPE["FORMAT_ERROR"])
                explanations.append(
                    f"[{name}] 指标未通过: score={score_str} < {threshold_str}"
                    + (f" — {reason}" if reason else "")
                )

    failure_types = list(dict.fromkeys(failure_types))

    # ══════════════════════════════════════════════════════════════════════
    # CASE-LEVEL FALLBACK: 对于 case.overall_status=FAILED 但 failure_types
    # 仍为空的情况（典型：score=0 时 judge reason 是自然语言段落、
    # 不带 rubric id 关键字），我们做一次启发式兜底推断，保证每个
    # FAILED case 至少有 1 种失败归因 —— 避免 FailureReport 漏计。
    # ══════════════════════════════════════════════════════════════════════
    if (not failure_types) and case.overall_status == "FAILED":
        expected = (case.expected_response or "").strip()
        actual = (case.actual_response or "").strip()
        exp_tools = case.expected_tool_calls or []
        act_tools = case.actual_tool_calls or []
        expected_tool_names = {t.get("name", "") for t in exp_tools if t}
        actual_tool_names = {t.get("name", "") for t in act_tools if t}
        missing_tools = expected_tool_names - actual_tool_names
        extra_tools = actual_tool_names - expected_tool_names

        msg_parts: list[str] = []

        # Tool discipline (highest priority: we ALWAYS have expected vs actual tool lists)
        if missing_tools:
            if any(t in missing_tools for t in ("check_stock", "get_discount",
                                                  "get_product_price", "get_shipping")):
                failure_types.append(FAILURE_TYPE["TOOL_CALL_ERROR"])
                msg_parts.append(f"[兜底] 缺少工具调用: {sorted(missing_tools)}")
            else:
                failure_types.append(FAILURE_TYPE["TOOL_CALL_ERROR"])
                msg_parts.append(f"[兜底] 缺少工具调用(可能为参数错配): {sorted(missing_tools)}")
        if extra_tools:
            failure_types.append(FAILURE_TYPE["TOOL_ARG_ERROR"])
            msg_parts.append(f"[兜底] 存在多余/越权工具调用: {sorted(extra_tools)}")

        # Format / verbosity / hallucination cues
        if actual and re.search(r"(```|###?\s+|\*\*|```json|```python|\|.*?\||\-\s+|^#)", actual, flags=re.MULTILINE):
            failure_types.append(FAILURE_TYPE["FORMAT_ERROR"])
            msg_parts.append("[兜底] 包含 Markdown/代码块/表格 格式输出")
        if "据我所知" in actual or "根据常识" in actual or "约 " in actual or "大概" in actual:
            failure_types.append(FAILURE_TYPE["HALLUCINATION"])
            msg_parts.append("[兜底] 包含常识/估计类表述（据我所知/根据常识/约/大概）")
        if ("推荐" in actual or "建议" in actual or "现在买最划算" in actual or "赶紧下单" in actual or "🎉" in actual):
            failure_types.append(FAILURE_TYPE["OVERGENERALIZATION"])
            msg_parts.append("[兜底] 包含推荐/促销话术/表情 emoji（用户未询问）")
        # Check repetition of expected numbers
        if actual:
            for num in re.findall(r"\d+(?:\.\d+)?", expected or ""):
                if actual.count(num) >= 3:
                    failure_types.append(FAILURE_TYPE["EXCESSIVE_VERBOSITY"])
                    msg_parts.append(f"[兜底] 数字 {num} 在回复中重复出现 3 次以上")
                    break
        if expected and actual and len(actual) > max(200, 2 * len(expected)):
            if FAILURE_TYPE["EXCESSIVE_VERBOSITY"] not in failure_types:
                failure_types.append(FAILURE_TYPE["EXCESSIVE_VERBOSITY"])
                msg_parts.append(f"[兜底] 回复过长: 长度 {len(actual)} vs 期望 {len(expected)}")

        # Grounding: contradiction (tool says X but answer says Y)
        # — example: shipping available=false but answer says "可以配送"
        actual_tool_returns_text = " ".join(
            [str(t.get("raw_return", "") if hasattr(t, "get") else "") for t in act_tools]
        )
        if actual_tool_returns_text:
            if ('"available": false' in actual_tool_returns_text or "'available': False" in actual_tool_returns_text or "available: false" in actual_tool_returns_text.lower()) and (
                "可以配送" in actual or "支持配送" in actual or "完全没有问题" in actual
            ):
                failure_types.append(FAILURE_TYPE["CONTRADICTORY_INFORMATION"])
                msg_parts.append("[兜底] 工具返回不可配送但回答声称可以配送（信息矛盾）")
            if '"status": "紧张"' in actual_tool_returns_text and "库存充足" in actual:
                failure_types.append(FAILURE_TYPE["CONTRADICTORY_INFORMATION"])
                msg_parts.append("[兜底] 工具返回库存紧张但回答说库存充足（信息矛盾）")

        # If we still have nothing, fall back to missing_information so
        # the case is never silently lost.
        if not failure_types:
            failure_types.append(FAILURE_TYPE["MISSING_INFORMATION"])
            msg_parts.append("[兜底] 整体未达标，未识别出更细类别的失败（默认归入信息遗漏）")

        if msg_parts:
            explanations = list(explanations)
            explanations.extend(msg_parts)

        failure_types = list(dict.fromkeys(failure_types))

    return FailureRecord(
        case_id=case.case_id, eval_set_id=eval_set_id,
        failure_types=failure_types,
        explanation="; ".join(explanations),
        metric_scores=scores,
    )


def analyze(baseline: BaselineResult) -> FailureReport:
    failures: list[FailureRecord] = []
    by_type: dict[str, int] = {}
    for case in baseline.per_case:
        record = _classify_case(case, baseline.eval_set_id)
        if record:
            failures.append(record)
            for ft in record.failure_types:
                by_type[ft] = by_type.get(ft, 0) + 1
    failures.sort(key=lambda r: len(r.failure_types), reverse=True)
    return FailureReport(total_failures=len(failures), by_type=by_type, per_case=failures)
