from __future__ import annotations

import json

from examples.optimization.eval_optimize_loop.eval_loop.fake_judge import FakeJudge
from examples.optimization.eval_optimize_loop.eval_loop.fake_model import FakeModel
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalCase


def test_fake_model_json_behavior_does_not_depend_on_case_id():
    case = EvalCase(
        case_id="hidden_json_case",
        split="validation",
        input="Return JSON.",
        expectation={"type": "json", "expected_values": {"status": "ok"}, "required_keys": ["status"]},
    )
    model = FakeModel(seed=91)

    baseline, _, _ = model.generate("baseline", "plain prompt", case)
    safe, _, _ = model.generate("safe", "OPTIMIZER_MARKER: STRICT_WHEN_REQUESTED", case)

    assert baseline.startswith("Here is the JSON")
    assert json.loads(safe) == {"status": "ok"}
    assert FakeJudge().score(case, baseline).passed is False
    assert FakeJudge().score(case, safe).passed is True


def test_fake_model_protected_exact_baseline_passes_but_overfit_regresses():
    case = EvalCase(
        case_id="hidden_protected_case",
        split="validation",
        input="Answer exactly YES.",
        expectation={"type": "exact", "expected": "YES"},
        protected=True,
    )
    model = FakeModel(seed=91)

    baseline, _, _ = model.generate("baseline", "plain prompt", case)
    overfit, _, _ = model.generate("overfit", "OPTIMIZER_MARKER: ALWAYS_OUTPUT_JSON", case)
    safe, _, _ = model.generate("safe", "OPTIMIZER_MARKER: STRICT_WHEN_REQUESTED", case)

    assert baseline == "YES"
    assert overfit == '{"answer": "YES"}'
    assert safe == "YES"


def test_fake_model_rubric_overfit_forces_json_on_validation_prose():
    case = EvalCase(
        case_id="hidden_rubric_case",
        split="validation",
        input="Explain in prose.",
        expectation={
            "type": "rubric",
            "must_include": ["cache", "stale data"],
            "forbidden": ["{", "}", "json"],
            "max_chars": 120,
        },
        tags=["prose"],
    )

    output, _, _ = FakeModel(seed=91).generate("overfit", "OPTIMIZER_MARKER: ALWAYS_OUTPUT_JSON", case)
    assert output.startswith("{")
    judged = FakeJudge().score(case, output)
    assert judged.error_code == "forbidden_pattern"


def test_fake_model_respects_simulated_outputs_override():
    case = EvalCase(
        case_id="override_case",
        split="train",
        input="Any",
        expectation={"type": "exact", "expected": "OK"},
        simulated_outputs={"safe": "CUSTOM"},
    )

    output, _, _ = FakeModel(seed=91).generate("safe", "OPTIMIZER_MARKER: STRICT_WHEN_REQUESTED", case)
    assert output == "CUSTOM"
