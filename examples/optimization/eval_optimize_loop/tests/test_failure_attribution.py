from __future__ import annotations

from examples.optimization.eval_optimize_loop.eval_loop.attribution import attribute_failure
from examples.optimization.eval_optimize_loop.eval_loop.attribution import summarize_failures
from examples.optimization.eval_optimize_loop.eval_loop.evaluator import ExampleEvaluator
from examples.optimization.eval_optimize_loop.eval_loop.fake_judge import FakeJudge
from examples.optimization.eval_optimize_loop.eval_loop.fake_model import FakeModel
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalCase


def test_failure_attribution_labels_format_violation_and_exact_mismatch():
    assert attribute_failure("json_parse_failure", "bad json")[0] == "format_violation"
    assert attribute_failure("exact_answer_mismatch", "wrong answer")[0] == "final_response_mismatch"


def test_evaluator_attaches_failure_details_to_failed_cases():
    cases = [
        EvalCase(
            case_id="json_case",
            split="train",
            input="Return JSON.",
            expectation={
                "type": "json",
                "required_keys": ["answer"],
                "expected_values": {"answer": "ok"},
            },
        ),
        EvalCase(
            case_id="exact_case",
            split="train",
            input="Answer exactly OK.",
            expectation={"type": "exact", "expected": "OK"},
        ),
    ]
    result = ExampleEvaluator(FakeModel(seed=91), FakeJudge()).evaluate(
        prompt_id="baseline",
        prompt="baseline prompt",
        cases=cases,
        split="train",
    )

    by_id = result.by_case_id()
    assert by_id["json_case"].failure_category == "format_violation"
    assert by_id["json_case"].failure_reason
    assert by_id["json_case"].evidence
    assert by_id["exact_case"].failure_category == "final_response_mismatch"
    assert by_id["exact_case"].failure_reason
    assert by_id["exact_case"].evidence


def test_failure_attribution_labels_tool_parameter_and_knowledge_failures():
    assert attribute_failure("tool_call_error", "wrong tool")[0] == "tool_call_error"
    assert attribute_failure("parameter_error", "wrong arg")[0] == "parameter_error"
    assert attribute_failure("knowledge_recall_insufficient", "missing source")[0] == "knowledge_recall_insufficient"


def test_fake_judge_scores_tool_and_parameter_expectations():
    judge = FakeJudge()
    tool_case = EvalCase(
        case_id="tool_case",
        split="train",
        input="Call tool",
        expectation={"type": "tool", "expected_tool": "lookup", "expected_args": {"id": "42"}},
    )

    assert judge.score(tool_case, '{"tool": "lookup", "args": {"id": "42"}}').passed
    wrong_tool = judge.score(tool_case, '{"tool": "search", "args": {"id": "42"}}')
    wrong_arg = judge.score(tool_case, '{"tool": "lookup", "args": {"id": "43"}}')
    assert wrong_tool.error_code == "tool_call_error"
    assert wrong_arg.error_code == "parameter_error"


def test_fake_judge_scores_knowledge_expectations():
    case = EvalCase(
        case_id="knowledge_case",
        split="train",
        input="Recall source",
        expectation={
            "type": "knowledge",
            "required_sources": ["doc-a"],
            "must_include_knowledge_terms": ["refund"],
        },
    )

    assert FakeJudge().score(case, "refund policy appears in doc-a").passed
    failed = FakeJudge().score(case, "refund policy only")
    assert failed.error_code == "knowledge_recall_insufficient"


def test_failure_summary_computes_attribution_accuracy():
    result = ExampleEvaluator(FakeModel(seed=91), FakeJudge()).evaluate(
        prompt_id="baseline",
        prompt="baseline prompt",
        split="train",
        cases=[
            EvalCase(
                case_id="json_labeled",
                split="train",
                input="Return JSON",
                expectation={"type": "json", "expected_values": {"answer": "ok"}},
                expected_failure_category="format_violation",
            )
        ],
    )

    summary = summarize_failures([result])
    assert summary["expected_labeled_failures"] == 1
    assert summary["attribution_accuracy"] == 1.0
