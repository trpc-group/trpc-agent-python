# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Metric reference doc builder for the optimize module.

Renders a structured markdown "syllabus" describing how each
user-configured metric is computed, for injection into the reflection
LM's prompt template alongside the per-case feedback.

The corpus is owned here (not delegated to each evaluator's
``get_metric_info()``) so wording can be tuned for what a rewriting LM
needs.

Coverage:
- Excludes tool/algorithm-fixed metrics (``tool_trajectory_avg_score``,
  ``response_match_score``, ``response_evaluation_score``).
- FinalResponseCriterion: text match modes / case sensitivity / ignore /
  JSON tree / numeric tolerance / AND combination / custom compare.
- LLMJudgeCriterion: single/multi judge / six built-in aggregators /
  parallel / rubrics / knowledge_tool_names / generation_config / think
  mode / weights.
"""

from __future__ import annotations

import math
from typing import Any
from typing import Optional

from ._eval_config import EvalConfig
from ._eval_metrics import EvalMetric
from ._eval_metrics import PrebuiltMetrics
from ._evaluator_registry import EVALUATOR_REGISTRY

_SKIPPED_METRICS: frozenset[str] = frozenset({
    PrebuiltMetrics.TOOL_TRAJECTORY_AVG_SCORE.value,
    PrebuiltMetrics.RESPONSE_MATCH_SCORE.value,
    PrebuiltMetrics.RESPONSE_EVALUATION_SCORE.value,
})

_METRIC_DESCRIPTIONS: dict[str, str] = {
    PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value: ("Deterministic match between the agent's final response and the "
                                                     "reference answer. Each invocation scores 1.0 (match) or 0.0 (no "
                                                     "match); the case score is the mean across invocations."),
    PrebuiltMetrics.LLM_FINAL_RESPONSE.value: ("An LLM judge inspects the agent's final response and returns a "
                                               "holistic valid/invalid verdict (1.0 or 0.0) together with a "
                                               "natural-language reason."),
    PrebuiltMetrics.LLM_RUBRIC_RESPONSE.value: ("An LLM judge scores the agent's final response against a list of "
                                                "rubric items. Each rubric is judged independently (0 or 1); the "
                                                "overall score is the mean of sub-scores. The judge returns a per-"
                                                "rubric reason explaining its verdict."),
    PrebuiltMetrics.LLM_RUBRIC_KNOWLEDGE_RECALL.value:
    ("An LLM judge inspects the knowledge content the agent retrieved via "
     "tool calls and scores it against a list of rubric items. Each "
     "rubric is judged independently (0 or 1); the overall score is the "
     "mean of sub-scores."),
}

_AGGREGATOR_EXPLANATIONS: dict[str, str] = {
    "all_pass": "all judges must PASS for the metric to PASS (strictest).",
    "any_pass": "any single judge passing is enough for the metric to PASS (most lenient).",
    "majority_pass": "more than half of the judges must PASS.",
    "avg": "arithmetic mean of judges' scores (uniform weighting).",
    "weighted_avg": "weighted mean of judges' scores using each model's ``weight``.",
    "weighted_majority": "weighted majority vote: passes when the weighted PASS vote exceeds the FAIL vote.",
}

_HEADER = ("## Metrics Reference\n\n"
           "The assistant's outputs are graded by the metrics below. UNDERSTAND THESE "
           "BEFORE PROPOSING CHANGES — they determine whether your new instruction "
           "improves or regresses the candidate.")

_FOOTER_GUIDELINES = ("## Rewriting Guidelines\n\n"
                      "1. **Preserve passing metrics.** A metric currently above its threshold "
                      "must not be sacrificed to fix a failing one.\n"
                      "2. **Use per-rubric sub-scores.** When a metric's per-case feedback "
                      "includes ``rubric_scores``, the failing sub-rubric tells you exactly "
                      "what's missing — and the passing ones tell you what to keep.\n"
                      "3. **Criterion-based metrics are deterministic.** The agent's output "
                      "must literally satisfy the matching rule (a ``contains`` rule means "
                      "the actual output has to include the expected substring verbatim).\n"
                      "4. **LLM-judged metrics evaluate qualities.** The judge reads each "
                      "rubric body literally. To lift a failing rubric you must instruct the "
                      "agent to visibly exhibit the quality that rubric describes.")


def build_metric_reference_doc(eval_config: EvalConfig) -> str:
    """Render the metric reference doc as markdown.

    Builds one section per user-configured criterion-based metric (skipping
    tool-call and algorithm-fixed metrics). Order is preserved from the user's
    configuration. Returns the header alone when no metric is eligible — the
    caller still gets a valid doc to inject.
    """
    metrics = eval_config.get_eval_metrics()
    included = [m for m in metrics if m.metric_name not in _SKIPPED_METRICS]

    if not included:
        return _HEADER + "\n\n_No graded metrics with criterion config are registered._\n"

    sections = [_HEADER]
    for metric in included:
        sections.append(build_metric_section(metric))
    sections.append(_FOOTER_GUIDELINES)

    return "\n\n".join(sections)


def build_metric_section(metric: EvalMetric) -> str:
    """Render a single metric's section.

    Public to keep tests focused: the section is also unit-testable
    independently of the surrounding header/footer.
    """
    name = metric.metric_name
    threshold = float(metric.threshold)
    criterion = metric.criterion or {}

    lines: list[str] = []
    lines.append(f"### Metric: `{name}`")
    lines.append("")
    lines.append(f"**Type**: {_metric_type(name)}")
    description = _METRIC_DESCRIPTIONS.get(name)
    if description:
        lines.append(f"**Description**: {description}")
    lines.append("")

    lines.append("**Scoring algorithm**:")
    if name == PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value:
        lines.extend(_render_final_response_criterion(criterion, metric_name=name))
    elif name in {
            PrebuiltMetrics.LLM_FINAL_RESPONSE.value,
            PrebuiltMetrics.LLM_RUBRIC_RESPONSE.value,
            PrebuiltMetrics.LLM_RUBRIC_KNOWLEDGE_RECALL.value,
    }:
        lines.extend(_render_llm_judge_criterion(criterion, metric_name=name))
    lines.append("")

    lines.append("**Score range**: 0.0 ~ 1.0")
    lines.append(f"**PASS condition**: score >= {threshold:.4f}")
    if name in {
            PrebuiltMetrics.LLM_RUBRIC_RESPONSE.value,
            PrebuiltMetrics.LLM_RUBRIC_KNOWLEDGE_RECALL.value,
    }:
        n_rubrics = _count_rubrics(criterion)
        if n_rubrics > 0:
            min_pass = math.ceil(threshold * n_rubrics)
            lines.append(f"  - With {n_rubrics} rubric item(s), at least **{min_pass}** must pass.")
    lines.append("")

    lines.append("**Per-case feedback contains**:")
    lines.extend(_render_feedback_fields(name))
    lines.append("")

    lines.append("**What reflection LM should know**:")
    lines.extend(_render_reflection_hints(name, criterion))

    return "\n".join(lines)


def _metric_type(name: str) -> str:
    if name == PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value:
        return "criterion-based (deterministic text and/or JSON match)"
    if name == PrebuiltMetrics.LLM_FINAL_RESPONSE.value:
        return "LLM-judged binary (valid/invalid)"
    if name == PrebuiltMetrics.LLM_RUBRIC_RESPONSE.value:
        return "LLM-judged rubric scoring (multiple sub-rubrics, score is the mean)"
    if name == PrebuiltMetrics.LLM_RUBRIC_KNOWLEDGE_RECALL.value:
        return "LLM-judged rubric scoring over knowledge-retrieval tool outputs"
    return "custom"


def _render_final_response_criterion(criterion: dict, *, metric_name: str) -> list[str]:
    out: list[str] = []

    if _has_custom_compare(metric_name):
        out.append("- **Custom compare function**: registered via "
                   "``EVALUATOR_REGISTRY.set_criterion_compare``. This callable "
                   "**overrides** all built-in text/JSON strategies below — the "
                   "agent's output is judged purely by user code.")
        return out

    fr = _pick(criterion, "final_response", "finalResponse")
    if not isinstance(fr, dict) or not fr:
        out.append("- _No ``final_response`` config provided; the metric will return 0.0 (FAIL)._")
        return out

    text = _pick(fr, "text", "text_strategy", "textStrategy")
    json_cfg = _pick(fr, "json", "json_strategy", "jsonStrategy")

    if isinstance(text, dict):
        out.extend(_render_text_strategy(text))
    if isinstance(json_cfg, dict):
        out.extend(_render_json_strategy(json_cfg))

    if isinstance(text, dict) and isinstance(json_cfg, dict):
        out.append("- **Combined**: both text and JSON checks must pass (AND logic). "
                   "A single failing check fails the case.")

    if not isinstance(text, dict) and not isinstance(json_cfg, dict):
        out.append("- _Neither text nor JSON strategy configured; the metric will FAIL by default._")

    return out


def _render_text_strategy(text: dict) -> list[str]:
    match = str(text.get("match") or text.get("match_strategy") or "exact").strip().lower()
    case_insensitive = bool(text.get("case_insensitive") or text.get("caseInsensitive"))
    ignored = bool(text.get("ignore"))

    if ignored:
        return ["- **Text comparison**: ``ignore=True`` — text check is skipped (always passes)"]

    mode_desc = {
        "exact": "actual output must be **byte-equal** to expected",
        "contains": "actual output must **contain** expected as a substring",
        "regex": "expected is treated as a **regular expression**; matched via ``re.search``",
    }.get(match, f"``{match}``")
    case_note = "case-insensitive" if case_insensitive else "case-sensitive"

    return [f"- **Text comparison** (``match=\"{match}\"``, {case_note}): {mode_desc}"]


def _render_json_strategy(json_cfg: dict) -> list[str]:
    if bool(json_cfg.get("ignore")):
        return ["- **JSON comparison**: ``ignore=True`` — JSON check is skipped"]

    out = ["- **JSON comparison**: actual and expected are parsed as JSON, then compared structurally"]
    ignore_tree = _pick(json_cfg, "ignore_tree", "ignoreTree")
    tolerance = _pick(json_cfg, "number_tolerance", "numberTolerance")
    if isinstance(ignore_tree, dict) and ignore_tree:
        out.append(f"  - Keys ignored before compare (``ignore_tree``): ``{ignore_tree}``")
    if tolerance is not None:
        out.append(f"  - Numeric tolerance: {tolerance}")
    else:
        out.append("  - Numeric tolerance: 1e-6 (default)")
    return out


def _render_llm_judge_criterion(criterion: dict, *, metric_name: str) -> list[str]:
    out: list[str] = []

    llm = _pick(criterion, "llm_judge", "llmJudge")
    if not isinstance(llm, dict) or not llm:
        out.append("- _No ``llm_judge`` config provided; the metric will fail to evaluate._")
        return out

    single = _pick(llm, "judge_model", "judgeModel")
    multi = _pick(llm, "judge_models", "judgeModels")

    if isinstance(multi, list) and multi:
        out.append(f"- **Judge models** ({len(multi)} judges, each scores independently):")
        for jm in multi:
            if isinstance(jm, dict):
                out.append("  - " + _format_judge_model(jm))
        agg = str(_pick(llm, "models_aggregator", "modelsAggregator") or "all_pass")
        agg_expl = _AGGREGATOR_EXPLANATIONS.get(agg, "custom aggregator (registered separately).")
        out.append(f"- **Cross-model aggregator** (``{agg}``): {agg_expl}")
        parallel = llm.get("parallel", True)
        par_text = ("yes (judges run concurrently)" if parallel else "no (judges run sequentially)")
        out.append(f"- **Parallel execution**: {par_text}")
    elif isinstance(single, dict):
        out.append(f"- **Judge model**: {_format_judge_model(single)}")
    else:
        out.append("- _No judge model configured._")

    rubrics = llm.get("rubrics") or []
    if isinstance(rubrics, list) and rubrics:
        out.append(f"- **Rubric items** ({len(rubrics)} items judged independently, each scored 0 or 1; "
                   "overall score = mean of sub-scores):")
        for i, rubric in enumerate(rubrics, 1):
            if not isinstance(rubric, dict):
                continue
            rid = rubric.get("id", f"rubric_{i}")
            desc = rubric.get("description", "")
            content = rubric.get("content") or {}
            body = content.get("text", "") if isinstance(content, dict) else ""
            head = f"  {i}. **``{rid}``**"
            if desc:
                head += f" — {desc}"
            out.append(head)
            if body:
                out.append(f"     > {body}")

    if metric_name == PrebuiltMetrics.LLM_RUBRIC_KNOWLEDGE_RECALL.value:
        knowledge_tools = _pick(llm, "knowledge_tool_names", "knowledgeToolNames")
        if isinstance(knowledge_tools, list) and knowledge_tools:
            out.append("- **Knowledge tools** (judge inspects results from these tool calls): "
                       f"``{', '.join(knowledge_tools)}``")
        else:
            out.append("- **Knowledge tools**: default knowledge tool set is used (no override).")

    return out


def _format_judge_model(jm: dict) -> str:
    model = jm.get("model_name") or jm.get("modelName") or "<unknown>"
    extras: list[str] = []

    num_samples = jm.get("num_samples") or jm.get("numSamples")
    if isinstance(num_samples, int) and num_samples > 1:
        extras.append(f"num_samples={num_samples}")

    gen = jm.get("generation_config") or jm.get("generationConfig") or {}
    if isinstance(gen, dict):
        if "temperature" in gen:
            extras.append(f"temperature={gen['temperature']}")
        mt = gen.get("max_tokens") or gen.get("maxTokens")
        if mt is not None:
            extras.append(f"max_tokens={mt}")

    weight = jm.get("weight")
    if isinstance(weight, (int, float)) and float(weight) != 1.0:
        extras.append(f"weight={weight}")

    think = jm.get("think")
    if think is True:
        extras.append("think=True")
    elif think is False:
        extras.append("think=False")

    base = f"``{model}``"
    if extras:
        return f"{base} ({', '.join(extras)})"
    return base


def _render_feedback_fields(metric_name: str) -> list[str]:
    out = ["- ``metric_name``, ``status`` (PASSED/FAILED), ``score``, ``threshold`` — always present"]
    if metric_name == PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value:
        out.append("- ``reason`` — short string (deterministic comparator; synthesized "
                   "from the criterion config when the matcher leaves it empty)")
        return out

    out.append("- ``reason`` — natural-language explanation written by the LLM judge")
    if metric_name in {
            PrebuiltMetrics.LLM_RUBRIC_RESPONSE.value,
            PrebuiltMetrics.LLM_RUBRIC_KNOWLEDGE_RECALL.value,
    }:
        out.append("- ``rubric_scores`` — per-rubric breakdown; each item has ``id``, "
                   "``score``, and a ``reason`` written by the judge")
    out.append("- ``per_model_scores`` (when multiple judge_models are configured) — "
               "each judge's independent score/reason")
    return out


def _render_reflection_hints(metric_name: str, criterion: dict) -> list[str]:
    out: list[str] = []

    if metric_name == PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value:
        if _has_custom_compare(metric_name):
            out.append("- Matching is delegated to user-provided Python code; format "
                       "requirements depend entirely on that comparator.")
            return out
        fr = _pick(criterion, "final_response", "finalResponse") or {}
        text = fr.get("text") if isinstance(fr, dict) else None
        match = ""
        if isinstance(text, dict):
            match = str(text.get("match") or text.get("match_strategy") or "exact").lower()
        if match == "exact":
            out.append("- Output must be **byte-exact**: stray whitespace or punctuation will FAIL.")
            out.append("- Prompt should constrain the agent to emit *only* the expected literal text "
                       "with no extra prose or formatting.")
        elif match == "contains":
            out.append("- Output must literally **contain** the expected substring.")
            out.append("- Prompt should drive the agent to emit that substring with correct "
                       "word order, punctuation, and units.")
        elif match == "regex":
            out.append("- Output is tested with ``re.search``; ensure the agent's response "
                       "satisfies the regex (think about how greediness and anchoring affect matching).")
        if isinstance(fr, dict) and (fr.get("json") or fr.get("json_strategy") or fr.get("jsonStrategy")):
            out.append("- JSON comparison is active; when the agent's output is parsed as JSON, "
                       "structural equality (after ``ignore_tree`` removal) matters.")
        return out

    if metric_name == PrebuiltMetrics.LLM_FINAL_RESPONSE.value:
        out.append("- The LLM judge gives a holistic verdict; read its ``reason`` for what swayed it.")
        out.append("- Align the prompt with the qualities the judge consistently rewards.")
        return out

    if metric_name in {
            PrebuiltMetrics.LLM_RUBRIC_RESPONSE.value,
            PrebuiltMetrics.LLM_RUBRIC_KNOWLEDGE_RECALL.value,
    }:
        out.append("- The judge reads each rubric body **literally**. To lift a failing rubric, "
                   "the agent's output must visibly satisfy what that rubric describes.")
        out.append("- Do NOT remove qualities currently scoring 1.0 — examine the passing "
                   "rubrics in the feedback and keep their requirements in your new prompt.")
        out.append("- When a rubric is being judged unfairly, prompt the agent to call out "
                   "the relevant quality explicitly so the judge cannot miss it.")
    return out


def _count_rubrics(criterion: dict) -> int:
    llm = _pick(criterion, "llm_judge", "llmJudge") or {}
    if not isinstance(llm, dict):
        return 0
    rubrics = llm.get("rubrics") or []
    if not isinstance(rubrics, list):
        return 0
    return len(rubrics)


def _has_custom_compare(metric_name: str) -> bool:
    """Detect whether a user-registered custom compare callable is present.

    Reads the registry's internal map by getattr (no public accessor exists);
    falls back to ``False`` if the attribute is missing or non-mapping.
    """
    registry = getattr(EVALUATOR_REGISTRY, "_criterion_compares", None)
    if not isinstance(registry, dict):
        return False
    return metric_name in registry


_REFLECTION_PROMPT_PREFIX = ("I provided an assistant with the following instruction(s):\n"
                             "```\n<curr_param>\n```\n")

_REFLECTION_PROMPT_MID_WITH_DOC = (
    "\n\nThe assistant's output is graded by the metrics described below. "
    "READ THEM CAREFULLY — every per-case feedback row references one of these metrics.\n\n")

_REFLECTION_PROMPT_MID_BARE = ("\n\nBelow are example inputs, the assistant's responses, and per-case feedback "
                               "summarising how each metric scored the response.\n\n")

_REFLECTION_PROMPT_FEEDBACK = ("## How to read each example\n\n"
                               "Every ``# Example N`` block below is a failed case rendered by GEPA "
                               "as nested markdown headers. The non-self-evident fields:\n\n"
                               "- ``## score`` is the case-level aggregate on [0, 1] (every metric, "
                               "every turn, every run rolled into one number); ``1.0`` would mean "
                               "every metric passed, so all examples here have ``score < 1.0``.\n"
                               "- ``## Case Body`` — a turn-sliced markdown block; the bulk of the "
                               "evidence lives here. Format described below.\n"
                               "- ``## Other Active Components`` *(present iff the candidate has "
                               "more than one prompt)* — the current text of every prompt OTHER "
                               "than the one you are about to rewrite (the target prompt is the "
                               "code-fenced block at the very top of this message). The verdict "
                               "you see was produced by the agent running with all prompts active, "
                               "so use these to:\n"
                               "  · avoid restating requirements already enforced elsewhere;\n"
                               "  · avoid contradicting another prompt's instructions;\n"
                               "  · spot gaps that no prompt currently covers.\n"
                               "- ``## history_top_k`` *(optional, present iff the case has prior "
                               "high-score runs from earlier candidates)* — a small list of "
                               "``{score, best_response}`` entries showing what previously scored well "
                               "on this case. Treat these as anchors: a rewrite that preserves the "
                               "pattern that produced those high scores is preferable to one that "
                               "regresses cases the optimizer already solved before.\n\n"
                               "## Case Body layout\n\n"
                               "``Case Body`` is a free-text markdown block. Each turn is one "
                               "``### Turn N`` section containing the conversational truth, the "
                               "agent's actual behaviour, and the per-turn verdict — kept together "
                               "so each failing metric is visually anchored to the turn that "
                               "produced it. Inside one turn:\n\n"
                               "```\n"
                               "### Turn N\n"
                               "**User**: <user query at turn N>\n"
                               "**Expected**: <reference answer at turn N>\n"
                               "**Agent Response**: <what the agent actually replied>\n"
                               "**Tool Trace**:                                  (omitted if no tools were used)\n"
                               "- <fn_name>(<arg>=<v>, ...) → <return value> [id=<call_id>]\n"
                               "**Verdict** (Turn N):\n"
                               "  [PASSED|FAILED] <metric_name>: score=<float>, threshold=<float>\n"
                               "    reason: <free text>\n"
                               "    · rubric[<id>]: PASS|FAIL score=<float>  reason: <free text>\n"
                               "```\n\n"
                               "Multi-run cases (``num_runs > 1``) nest each run inside the turn:\n\n"
                               "```\n"
                               "### Turn N\n"
                               "**User**: ...\n"
                               "**Expected**: ...\n"
                               "\n"
                               "#### Run 1\n"
                               "**Agent Response**: ...\n"
                               "**Tool Trace**: ...\n"
                               "**Verdict** (Turn N, Run 1):\n"
                               "  ...\n"
                               "\n"
                               "#### Run 2\n"
                               "...\n"
                               "```\n\n"
                               "Multi-turn or multi-run cases close with an ``### Overall`` block "
                               "(``### Overall (case-level aggregate)`` for single-run, "
                               "``### Overall (per-run aggregate)`` for multi-run). Single-turn "
                               "single-run cases skip the Overall block because Turn 1 already "
                               "carries the only verdict that exists.\n\n"
                               "## Reading rules\n\n"
                               "- The reference answer ONLY appears in ``**Expected**``; it is "
                               "deliberately not echoed inside the Verdict line, so do not look for "
                               "it there.\n"
                               "- Every ``<metric_name>`` in a Verdict line maps directly to a "
                               "``### Metric: <metric_name>`` section in the Metrics Reference above "
                               "— consult it for how the score is computed before deciding what to "
                               "change.\n"
                               "- Treat PASSING metrics as constraints, not noise: a rewrite that "
                               "fixes a FAILING metric while regressing a PASSING one is a "
                               "regression, not an improvement.\n\n"
                               "Examples follow:\n"
                               "```\n<side_info>\n```\n\n"
                               "Read each example end-to-end, then rewrite the instruction so PASSING "
                               "metrics stay passing and FAILING metrics improve. Provide the new "
                               "instruction inside ``` blocks.\n")


def build_reflection_prompt_template(metric_reference_doc: str) -> str:
    """Build the prompt template handed to GEPA's reflection LM.

    GEPA fills ``<curr_param>`` with the current prompt text and ``<side_info>``
    with the rendered per-case feedback. The metric reference doc is wedged
    between them so the LM has: (1) the current prompt, (2) a static metric
    syllabus, (3) live per-case feedback, in that order.

    GEPA's ``InstructionProposalSignature.validate_prompt_template`` enforces
    that both placeholders are present, so we always keep them — even when
    ``metric_reference_doc`` is empty.
    """
    doc = (metric_reference_doc or "").strip()
    if doc:
        middle = _REFLECTION_PROMPT_MID_WITH_DOC + doc + "\n\n"
    else:
        middle = _REFLECTION_PROMPT_MID_BARE
    return _REFLECTION_PROMPT_PREFIX + middle + _REFLECTION_PROMPT_FEEDBACK


def _pick(d: dict, *keys: str) -> Optional[Any]:
    """Return the first present value among ``keys`` (handles camelCase/snake_case aliases)."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d:
            return d[k]
    return None
