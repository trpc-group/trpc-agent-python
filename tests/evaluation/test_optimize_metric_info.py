# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the optimize-side metric reference doc builder.

The doc is the static "syllabus" injected into the reflection LM's prompt
template alongside the dynamic per-case feedback. Every code path tested
here describes a knob the user can turn in optimizer.json's
``evaluate.metrics[]`` array, and the doc must render that knob so the
reflection LM understands how the metric scores its rewrites.
"""

from __future__ import annotations

import math

import pytest

from trpc_agent_sdk.evaluation._eval_config import EvalConfig
from trpc_agent_sdk.evaluation._optimize_metric_info import (
    build_metric_reference_doc,
    build_metric_section,
    build_reflection_prompt_template,
)


def _config_with(metric_dicts: list[dict]) -> EvalConfig:
    """Wrap a list of metric dicts into an EvalConfig (Pydantic round-trip safe)."""
    return EvalConfig(metrics=metric_dicts, num_runs=1)


# -------- Exclusion rules --------


def test_skip_tool_trajectory_metric():
    cfg = _config_with([
        {"metric_name": "tool_trajectory_avg_score", "threshold": 1.0},
    ])
    doc = build_metric_reference_doc(cfg)
    assert "tool_trajectory_avg_score" not in doc


def test_skip_rouge_metric():
    cfg = _config_with([
        {"metric_name": "response_match_score", "threshold": 0.5},
    ])
    doc = build_metric_reference_doc(cfg)
    assert "response_match_score" not in doc


def test_empty_metrics_renders_placeholder():
    cfg = _config_with([])
    doc = build_metric_reference_doc(cfg)
    assert doc.strip()  # non-empty header at minimum


# -------- final_response_avg_score --------


def test_final_response_text_contains_case_insensitive():
    cfg = _config_with([{
        "metric_name": "final_response_avg_score",
        "threshold": 1.0,
        "criterion": {
            "final_response": {
                "text": {"match": "contains", "case_insensitive": True}
            }
        },
    }])
    doc = build_metric_reference_doc(cfg)
    assert "final_response_avg_score" in doc
    assert "contains" in doc
    assert "case-insensitive" in doc.lower()
    assert "1.0000" in doc  # threshold rendered


def test_final_response_text_exact_case_sensitive():
    cfg = _config_with([{
        "metric_name": "final_response_avg_score",
        "threshold": 1.0,
        "criterion": {"final_response": {"text": {"match": "exact"}}},
    }])
    doc = build_metric_reference_doc(cfg)
    assert "exact" in doc
    assert "case-sensitive" in doc.lower()


def test_final_response_text_regex_mode():
    cfg = _config_with([{
        "metric_name": "final_response_avg_score",
        "threshold": 1.0,
        "criterion": {"final_response": {"text": {"match": "regex"}}},
    }])
    doc = build_metric_reference_doc(cfg)
    assert "regex" in doc
    assert "re.search" in doc or "regular expression" in doc.lower()


def test_final_response_text_ignored():
    cfg = _config_with([{
        "metric_name": "final_response_avg_score",
        "threshold": 1.0,
        "criterion": {"final_response": {"text": {"match": "exact", "ignore": True}}},
    }])
    doc = build_metric_reference_doc(cfg)
    # ignore=True means text comparison is skipped
    assert "skipped" in doc.lower() or "ignore" in doc.lower()


def test_final_response_json_with_ignore_tree_and_tolerance():
    cfg = _config_with([{
        "metric_name": "final_response_avg_score",
        "threshold": 1.0,
        "criterion": {
            "final_response": {
                "json": {
                    "ignore_tree": {"id": True, "meta": {"ts": True}},
                    "number_tolerance": 0.001,
                }
            }
        },
    }])
    doc = build_metric_reference_doc(cfg)
    assert "JSON" in doc
    assert "ignore_tree" in doc or "ignored" in doc.lower()
    assert "0.001" in doc


def test_final_response_text_and_json_combined_uses_and_logic():
    cfg = _config_with([{
        "metric_name": "final_response_avg_score",
        "threshold": 1.0,
        "criterion": {
            "final_response": {
                "text": {"match": "exact"},
                "json": {"number_tolerance": 0.01},
            }
        },
    }])
    doc = build_metric_reference_doc(cfg)
    assert "AND" in doc or "both" in doc.lower()


def test_final_response_custom_compare_overrides_text_and_json():
    """When the user registers a custom compare via EVALUATOR_REGISTRY,
    the doc must explicitly tell the reflection LM that text/json
    strategies are overridden by user code."""
    from trpc_agent_sdk.evaluation._evaluator_registry import EVALUATOR_REGISTRY

    def my_compare(actual, expected):  # pragma: no cover - registered then removed
        return True

    EVALUATOR_REGISTRY.set_criterion_compare("final_response_avg_score", my_compare)
    try:
        cfg = _config_with([{
            "metric_name": "final_response_avg_score",
            "threshold": 1.0,
            "criterion": {"final_response": {"text": {"match": "exact"}}},
        }])
        doc = build_metric_reference_doc(cfg)
        assert "custom" in doc.lower()
        assert "override" in doc.lower()
    finally:
        # cleanup: this is a global registry, leaking would affect later tests
        EVALUATOR_REGISTRY._criterion_compares.pop("final_response_avg_score", None)


# -------- llm_rubric_response --------


def test_llm_rubric_single_judge_with_rubrics():
    cfg = _config_with([{
        "metric_name": "llm_rubric_response",
        "threshold": 0.66,
        "criterion": {
            "llm_judge": {
                "judge_model": {
                    "model_name": "glm-5.1-w4afp8",
                    "num_samples": 1,
                    "generation_config": {"max_tokens": 1024, "temperature": 0.2},
                },
                "rubrics": [
                    {
                        "id": "numeric_correct",
                        "description": "数字答案与参考答案一致",
                        "content": {"text": "最终给出的数字答案是否与参考答案一致。"},
                    },
                    {
                        "id": "reasoning_clear",
                        "description": "推理步骤清晰",
                        "content": {"text": "回答中是否给出清晰的推理过程。"},
                    },
                ],
            }
        },
    }])
    doc = build_metric_reference_doc(cfg)
    assert "llm_rubric_response" in doc
    assert "glm-5.1-w4afp8" in doc
    assert "numeric_correct" in doc
    assert "数字答案与参考答案一致" in doc
    assert "reasoning_clear" in doc
    assert "0.6600" in doc
    # judge config fields surfaced
    assert "temperature=0.2" in doc
    assert "max_tokens=1024" in doc


def test_llm_rubric_multi_judge_with_weighted_avg():
    cfg = _config_with([{
        "metric_name": "llm_rubric_response",
        "threshold": 0.5,
        "criterion": {
            "llm_judge": {
                "judge_models": [
                    {"model_name": "judge-A", "weight": 2.0},
                    {"model_name": "judge-B", "weight": 1.0},
                ],
                "models_aggregator": "weighted_avg",
                "parallel": True,
                "rubrics": [
                    {"id": "r1", "description": "d1", "content": {"text": "rubric text 1"}},
                ],
            }
        },
    }])
    doc = build_metric_reference_doc(cfg)
    assert "judge-A" in doc
    assert "judge-B" in doc
    assert "weight=2.0" in doc
    assert "weighted_avg" in doc
    assert "parallel" in doc.lower()


@pytest.mark.parametrize("aggregator,must_contain", [
    ("all_pass", "all"),
    ("any_pass", "any"),
    ("majority_pass", "majority"),
    ("avg", "mean"),
    ("weighted_avg", "weighted"),
    ("weighted_majority", "weighted"),
])
def test_each_aggregator_has_explanation(aggregator, must_contain):
    cfg = _config_with([{
        "metric_name": "llm_rubric_response",
        "threshold": 0.5,
        "criterion": {
            "llm_judge": {
                "judge_models": [
                    {"model_name": "j1", "weight": 1.0},
                    {"model_name": "j2", "weight": 1.0},
                ],
                "models_aggregator": aggregator,
                "rubrics": [{"id": "r1", "description": "d", "content": {"text": "x"}}],
            }
        },
    }])
    doc = build_metric_reference_doc(cfg)
    assert aggregator in doc
    assert must_contain.lower() in doc.lower()


def test_llm_rubric_threshold_translates_to_min_pass_count():
    cfg = _config_with([{
        "metric_name": "llm_rubric_response",
        "threshold": 0.66,
        "criterion": {
            "llm_judge": {
                "judge_model": {"model_name": "j1"},
                "rubrics": [
                    {"id": f"r{i}", "description": "d", "content": {"text": "x"}}
                    for i in range(3)
                ],
            }
        },
    }])
    doc = build_metric_reference_doc(cfg)
    # 0.66 * 3 = 1.98 -> ceil = 2; reflection LM needs to see this concretely
    min_pass = math.ceil(0.66 * 3)
    assert str(min_pass) in doc


# -------- llm_rubric_knowledge_recall --------


def test_llm_rubric_knowledge_recall_renders_tool_names():
    cfg = _config_with([{
        "metric_name": "llm_rubric_knowledge_recall",
        "threshold": 0.5,
        "criterion": {
            "llm_judge": {
                "judge_model": {"model_name": "j1"},
                "rubrics": [{"id": "kr1", "description": "d", "content": {"text": "k"}}],
                "knowledge_tool_names": ["search_docs", "retrieve_chunks"],
            }
        },
    }])
    doc = build_metric_reference_doc(cfg)
    assert "search_docs" in doc
    assert "retrieve_chunks" in doc
    assert "knowledge" in doc.lower()


def test_llm_rubric_knowledge_recall_default_tools_noted_when_unset():
    cfg = _config_with([{
        "metric_name": "llm_rubric_knowledge_recall",
        "threshold": 0.5,
        "criterion": {
            "llm_judge": {
                "judge_model": {"model_name": "j1"},
                "rubrics": [{"id": "kr1", "description": "d", "content": {"text": "k"}}],
            }
        },
    }])
    doc = build_metric_reference_doc(cfg)
    # default knowledge tool set should be mentioned
    assert "default" in doc.lower()


# -------- llm_final_response --------


def test_llm_final_response_binary_judge():
    cfg = _config_with([{
        "metric_name": "llm_final_response",
        "threshold": 1.0,
        "criterion": {
            "llm_judge": {
                "judge_model": {"model_name": "j1"},
            }
        },
    }])
    doc = build_metric_reference_doc(cfg)
    assert "llm_final_response" in doc
    assert "binary" in doc.lower() or "valid" in doc.lower()


# -------- Cross-cutting --------


def test_metrics_listed_in_user_configured_order():
    cfg = _config_with([
        {
            "metric_name": "llm_rubric_response",
            "threshold": 0.5,
            "criterion": {"llm_judge": {
                "judge_model": {"model_name": "j1"},
                "rubrics": [{"id": "r1", "description": "d", "content": {"text": "x"}}],
            }},
        },
        {
            "metric_name": "final_response_avg_score",
            "threshold": 1.0,
            "criterion": {"final_response": {"text": {"match": "exact"}}},
        },
    ])
    doc = build_metric_reference_doc(cfg)
    assert doc.index("llm_rubric_response") < doc.index("final_response_avg_score")


def test_doc_contains_per_case_feedback_field_list():
    cfg = _config_with([{
        "metric_name": "llm_rubric_response",
        "threshold": 0.5,
        "criterion": {"llm_judge": {
            "judge_model": {"model_name": "j1"},
            "rubrics": [{"id": "r1", "description": "d", "content": {"text": "x"}}],
        }},
    }])
    doc = build_metric_reference_doc(cfg)
    # rubric metric must tell the LM that rubric_scores appear in per-case feedback
    assert "rubric_scores" in doc
    assert "reason" in doc


def test_doc_contains_rewriting_guidelines_section():
    cfg = _config_with([{
        "metric_name": "final_response_avg_score",
        "threshold": 1.0,
        "criterion": {"final_response": {"text": {"match": "exact"}}},
    }])
    doc = build_metric_reference_doc(cfg)
    # the footer "rewriting rules" is essential — it tells the LM how to use
    # the per-metric info above when proposing changes
    assert "Rewriting" in doc or "Guideline" in doc or "Preserve" in doc


def test_build_metric_section_returns_markdown_for_single_metric():
    from trpc_agent_sdk.evaluation._eval_metrics import EvalMetric

    metric = EvalMetric(
        metric_name="final_response_avg_score",
        threshold=1.0,
        criterion={"final_response": {"text": {"match": "contains"}}},
    )
    section = build_metric_section(metric)
    assert "final_response_avg_score" in section
    assert "contains" in section
    assert "1.0000" in section


def test_quickstart_config_renders_complete_doc():
    """End-to-end smoke test using a close clone of quickstart/optimizer.json."""
    cfg = _config_with([
        {
            "metric_name": "final_response_avg_score",
            "threshold": 1.0,
            "criterion": {
                "final_response": {"text": {"match": "contains", "case_insensitive": True}}
            },
        },
        {
            "metric_name": "llm_rubric_response",
            "threshold": 0.66,
            "criterion": {"llm_judge": {
                "judge_model": {
                    "model_name": "glm-5.1-w4afp8",
                    "num_samples": 1,
                    "generation_config": {"max_tokens": 1024, "temperature": 0.2},
                },
                "rubrics": [
                    {"id": "numeric_correct", "description": "数字答案与参考答案一致",
                     "content": {"text": "最终给出的数字答案是否与参考答案一致。"}},
                    {"id": "reasoning_clear", "description": "推理步骤清晰",
                     "content": {"text": "回答中是否给出清晰、可追溯的推理或计算步骤。"}},
                    {"id": "units_present", "description": "答案带正确单位",
                     "content": {"text": "最终数字答案是否带有正确的单位。"}},
                ],
            }},
        },
    ])
    doc = build_metric_reference_doc(cfg)

    # Both metrics surface
    assert "final_response_avg_score" in doc
    assert "llm_rubric_response" in doc

    # final_response_avg_score config knobs
    assert "contains" in doc
    assert "case-insensitive" in doc.lower()

    # llm_rubric_response judge config
    assert "glm-5.1-w4afp8" in doc
    assert "temperature=0.2" in doc

    # All three rubrics with their bodies
    for rid in ("numeric_correct", "reasoning_clear", "units_present"):
        assert rid in doc

    # Thresholds rendered
    assert "1.0000" in doc
    assert "0.6600" in doc

    # Min-pass count for rubric metric (ceil(0.66 * 3) = 2)
    assert " 2" in doc or "2 " in doc


# -------- build_reflection_prompt_template --------


def test_reflection_prompt_template_keeps_required_placeholders():
    """GEPA validates the template — both <curr_param> and <side_info>
    must remain or gepa.optimize raises."""
    template = build_reflection_prompt_template("## Metrics Reference\n\n_dummy_")
    assert "<curr_param>" in template
    assert "<side_info>" in template


def test_reflection_prompt_template_embeds_metric_doc_between_placeholders():
    metric_doc = "## Metrics Reference\n\nMARKER_FOR_TEST\n"
    template = build_reflection_prompt_template(metric_doc)
    assert "MARKER_FOR_TEST" in template
    # placement: metric doc sits AFTER <curr_param> (current prompt) so the LM
    # has the current text first, then learns the metrics, then sees feedback
    assert template.index("<curr_param>") < template.index("MARKER_FOR_TEST")
    assert template.index("MARKER_FOR_TEST") < template.index("<side_info>")


def test_reflection_prompt_template_handles_empty_metric_doc():
    """When metric_doc is empty (no eligible metrics), template still must be
    a valid GEPA template — placeholders intact, no spurious markdown."""
    template = build_reflection_prompt_template("")
    assert "<curr_param>" in template
    assert "<side_info>" in template
    # GEPA will validate; no exception means template is well-formed


def test_reflection_prompt_template_does_not_inline_describe_self_evident_fields():
    """GEPA's prompt_renderer emits every record-dict key as ``## <key>``
    markdown header automatically. For keys whose meaning is self-evident
    from the header alone (``case_id`` — obviously an identifier), our
    static template must NOT re-narrate them ahead of ``<side_info>``.

    The template is allowed (and expected) to keep semantic guidance GEPA
    cannot infer from markdown alone: the score's [0, 1] aggregate range,
    the ``Case Body`` inner turn-sliced format, the ``Tool Trace`` line
    grammar, and the ``Other Active Components`` cross-component context.
    """
    template = build_reflection_prompt_template("## Metrics Reference\n\n_dummy_")
    pre_side_info = template.split("<side_info>", 1)[0]

    # case_id should be left fully self-evident — the header name says it
    # all, no narration needed.
    forbidden_phrases = (
        "stable identifier for the case",
        "stable id for the case",
        "unique id for the case",
    )
    for phrase in forbidden_phrases:
        assert phrase not in pre_side_info, (
            f"static template still inline-describes a self-evident field "
            f"via phrase {phrase!r}; GEPA's auto-rendered ``## case_id`` "
            f"header already conveys this — remove the narration"
        )


def test_reflection_prompt_template_documents_score_aggregate_range():
    """``score`` is the case-level aggregate on [0, 1] — not a per-metric
    score and not the threshold. The template must clarify this so the LM
    does not confuse the case score with the per-metric scores inside the
    Verdict lines."""
    template = build_reflection_prompt_template("## Metrics Reference\n\n_dummy_")
    pre_side_info = template.split("<side_info>", 1)[0]
    assert "[0, 1]" in pre_side_info
    assert "case-level" in pre_side_info.lower() or "case level" in pre_side_info.lower()


def test_reflection_prompt_template_documents_case_body_turn_layout():
    """``Case Body`` is a free-text markdown string; GEPA dumps it as-is.
    The static template must spell out the ``### Turn N`` header layout,
    the ``**User**``/``**Expected**``/``**Agent Response**``/``**Verdict**``
    field markers, and the per-metric line grammar — otherwise the LM has
    to reverse-engineer the convention from raw text."""
    template = build_reflection_prompt_template("## Metrics Reference\n\n_dummy_")
    assert "### Turn N" in template
    assert "**User**" in template
    assert "**Expected**" in template
    assert "**Agent Response**" in template
    assert "**Verdict**" in template
    assert "[PASSED|FAILED]" in template
    assert "threshold=" in template
    assert "rubric[" in template


def test_reflection_prompt_template_documents_multi_run_nested_run_blocks():
    """Multi-run cases nest ``#### Run N`` inside each turn; the template
    must announce this layout up front so the LM knows variance is
    attributable per run rather than averaged out."""
    template = build_reflection_prompt_template("## Metrics Reference\n\n_dummy_")
    assert "#### Run" in template
    assert "num_runs" in template.lower() or "multi-run" in template.lower()


def test_reflection_prompt_template_documents_tool_trace_line_grammar():
    """``Tool Trace`` lines are rendered inline (``func(arg=val) → result
    [id=...]``) instead of nested dict headers — the template must
    document the line grammar because GEPA's renderer cannot infer it."""
    template = build_reflection_prompt_template("## Metrics Reference\n\n_dummy_")
    assert "Tool Trace" in template
    # The line skeleton must be visible so the LM knows how to parse it.
    assert "fn_name" in template or "<fn_name>" in template
    assert "→" in template
    assert "[id=" in template


def test_reflection_prompt_template_documents_other_active_components_semantics():
    """``Other Active Components`` is the cross-component context: every
    OTHER prompt's current body, present iff the candidate has more than
    one prompt. The template must explain that:
      - the LM only sees the target prompt at the top of the message
      - the verdict came from ALL prompts running together
    so the LM uses these contents to avoid duplication and contradiction.
    The template must NOT mention ``<curr_param>`` by name because GEPA's
    prompt_renderer substitutes that placeholder everywhere it appears
    in the template, leaking the prompt text into the documentation."""
    template = build_reflection_prompt_template("## Metrics Reference\n\n_dummy_")
    pre_side_info = template.split("<side_info>", 1)[0]
    assert "Other Active Components" in pre_side_info
    # Regression guard: never document ``<curr_param>`` by name in the
    # static template, otherwise it gets substituted into garbage.
    assert "<curr_param>" not in pre_side_info.replace(
        "```\n<curr_param>\n```", ""
    ).replace("<curr_param>", "", 1) or True  # placeholder usage is fine
    # The actual regression assertion: the substring shouldn't appear
    # twice in the pre-side-info region (once for placeholder, never in narration).
    assert pre_side_info.count("<curr_param>") == 1, (
        "``<curr_param>`` should appear exactly once in the template "
        "(the placeholder itself); referencing it in narration causes "
        "GEPA's prompt_renderer to leak the prompt text into the docs."
    )
    # The cross-component intent must surface, regardless of exact wording.
    lowered = pre_side_info.lower()
    assert (
        "avoid restating" in lowered
        or "avoid contradicting" in lowered
        or "all prompts" in lowered
    )


def test_reflection_prompt_template_warns_against_regressing_passing_metrics():
    """A rewrite that fixes a FAILING metric but regresses a PASSING one
    is a regression, not progress. The template must surface this rule so
    the LM treats PASSING metrics as hard constraints, not noise."""
    template = build_reflection_prompt_template("## Metrics Reference\n\n_dummy_")
    lowered = template.lower()
    assert (
        "passing metrics stay passing" in lowered
        or "passing metrics as constraints" in lowered
        or "regressing a passing" in lowered
    )


def test_reflection_prompt_template_documents_history_top_k() -> None:
    """The reflection LM must be told how to read history_top_k."""
    from trpc_agent_sdk.evaluation._optimize_metric_info import build_reflection_prompt_template

    template = build_reflection_prompt_template("")

    assert "## history_top_k" in template or "``## history_top_k``" in template
    assert "preserve" in template.lower() or "anchor" in template.lower()


def test_reflection_prompt_template_explains_history_top_k_is_optional() -> None:
    from trpc_agent_sdk.evaluation._optimize_metric_info import build_reflection_prompt_template

    template = build_reflection_prompt_template("")

    assert "present iff" in template or "optional" in template.lower()
