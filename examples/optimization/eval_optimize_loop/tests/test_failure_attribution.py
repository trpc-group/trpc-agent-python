from __future__ import annotations

from examples.optimization.eval_optimize_loop.eval_loop.attribution import attribute_failure
from examples.optimization.eval_optimize_loop.eval_loop.evaluator import ExampleEvaluator
from examples.optimization.eval_optimize_loop.eval_loop.fake_judge import FakeJudge
from examples.optimization.eval_optimize_loop.eval_loop.fake_model import FakeModel
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalCase


def test_failure_attribution_labels_format_violation_and_exact_mismatch():
    assert attribute_failure("json_parse_failure", "bad json")[0] == "format_violation"
    assert attribute_failure("exact_answer_mismatch", "wrong answer")[0] == "final_answer_mismatch"


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
    assert by_id["exact_case"].failure_category == "final_answer_mismatch"
    assert by_id["exact_case"].failure_reason
    assert by_id["exact_case"].evidence
