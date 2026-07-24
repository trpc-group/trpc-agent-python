from __future__ import annotations

import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest
from trpc_agent_sdk.evaluation import EvalSet

from examples.optimization.eval_optimize_loop.eval_loop import fake_model as fake_model_module
from examples.optimization.eval_optimize_loop.eval_loop.backends import FakeBackend
from examples.optimization.eval_optimize_loop.eval_loop.evaluator import ExampleEvaluator
from examples.optimization.eval_optimize_loop.eval_loop.fake_judge import FakeJudge
from examples.optimization.eval_optimize_loop.eval_loop.fake_model import FakeModel
from examples.optimization.eval_optimize_loop.eval_loop.optimizer import FakeOptimizer
from examples.optimization.eval_optimize_loop.eval_loop.schemas import CaseResult
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalCase
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalResult

ROOT = Path(__file__).resolve().parents[1]
TRAIN_PATH = ROOT / "data" / "train.evalset.json"
VALIDATION_PATH = ROOT / "data" / "val.evalset.json"

BASELINE_PROMPT = "Answer the user's request."
OVERFIT_INSTRUCTION = "Always force every final answer into JSON"
SAFE_INSTRUCTION = "Use strict JSON only when the user explicitly asks"
OVERFIT_PROMPT = f"{BASELINE_PROMPT}\n\n{OVERFIT_INSTRUCTION}."
SAFE_PROMPT = f"{BASELINE_PROMPT}\n\n{SAFE_INSTRUCTION}."


def test_model_output_and_trace_are_stable_for_the_same_public_input():
    public_input = "Return strict JSON with intent=refund and priority=high."
    same_inputs = [public_input, f"{public_input}", "".join([public_input])]

    generated = [FakeModel(seed=91).generate("candidate", SAFE_PROMPT, user_input) for user_input in same_inputs]

    assert {output for output, _, _ in generated} == {'{"intent": "refund", "priority": "high"}'}
    assert {json.dumps(trace, sort_keys=True)
            for _, trace, _ in generated
            } == {json.dumps(
                {
                    "seed": 91,
                    "prompt_id": "candidate",
                    "prompt_mode": "safe"
                },
                sort_keys=True,
            )}


def test_example_evaluator_only_exposes_public_user_input_to_model():

    class SpyModel:

        def __init__(self) -> None:
            self.user_inputs: list[str] = []

        def generate(
            self,
            prompt_id: str,
            prompt: str,
            user_input: str,
        ) -> tuple[str, dict[str, object], float]:
            assert isinstance(user_input, str)
            self.user_inputs.append(user_input)
            return "SECRET_EXPECTATION", {"prompt_id": prompt_id, "prompt": prompt}, 0.0

    spy = SpyModel()
    secret_case = EvalCase(
        case_id="SECRET_CASE_ID",
        split="validation",
        input="PUBLIC USER INPUT",
        expectation={
            "type": "exact",
            "expected": "SECRET_EXPECTATION"
        },
        tags=["SECRET_TAG"],
        protected=True,
        simulated_outputs={"safe": "SECRET_SIMULATED_OUTPUT"},
    )

    result = ExampleEvaluator(spy, FakeJudge()).evaluate(
        prompt_id="candidate",
        prompt="PUBLIC SYSTEM PROMPT",
        cases=[secret_case],
        split="validation",
    )

    assert result.passed is True
    assert spy.user_inputs == ["PUBLIC USER INPUT"]
    assert "SECRET" not in json.dumps(spy.user_inputs)


def test_fake_model_source_has_no_evaluator_oracle_capability():
    model_source = inspect.getsource(fake_model_module)
    evaluator_source = inspect.getsource(ExampleEvaluator.evaluate)

    assert "EvalCase" not in model_source
    assert ".expectation" not in model_source
    assert ".tags" not in model_source
    assert ".protected" not in model_source
    assert ".simulated_outputs" not in model_source
    assert "self.model.generate(prompt_id, prompt, case.input)" in evaluator_source


@pytest.mark.parametrize("user_input", [None, b"bytes", object()])
def test_fake_model_rejects_non_string_user_input(user_input: object):
    with pytest.raises(TypeError, match="^user_input must be a string$"):
        FakeModel(seed=91).generate("candidate", SAFE_PROMPT, user_input)


def test_model_is_deterministic_and_prompt_id_or_seed_cannot_change_output():
    user_input = "Return strict JSON with status=READY and next_step=ship."
    model = FakeModel(seed=91)

    first = model.generate("first-id", SAFE_PROMPT, user_input)
    repeated = model.generate("first-id", SAFE_PROMPT, user_input)
    different_prompt_id = model.generate("second-id", SAFE_PROMPT, user_input)
    different_seed = FakeModel(seed=999).generate("first-id", SAFE_PROMPT, user_input)

    assert first == repeated
    assert first[0] == different_prompt_id[0] == different_seed[0]
    assert first[0] == '{"next_step": "ship", "status": "READY"}'


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (
            BASELINE_PROMPT,
            'Here is the JSON you requested: {"intent": "refund", "priority": "high"}',
        ),
        (OVERFIT_PROMPT, '{"intent": "refund", "priority": "high"}'),
        (SAFE_PROMPT, '{"intent": "refund", "priority": "high"}'),
    ],
)
def test_strict_json_rendering_uses_assignments_from_user_input(
    prompt: str,
    expected: str,
):
    user_input = "Return StRiCt JsOn with intent=refund and priority=high."

    output, _, _ = FakeModel(seed=91).generate("arbitrary-id", prompt, user_input)

    assert output == expected


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (BASELINE_PROMPT, "YES"),
        (SAFE_PROMPT, "YES"),
        (OVERFIT_PROMPT, '{"answer": "YES"}'),
    ],
)
def test_return_only_rendering_is_prompt_mode_sensitive(prompt: str, expected: str):
    user_input = "ReTuRn OnLy YES; do not use JSON."

    output, _, _ = FakeModel(seed=91).generate("arbitrary-id", prompt, user_input)

    assert output == expected


@pytest.mark.parametrize(
    ("input_text", "natural"),
    [
        (
            "Explain latency and retries naturally in under 80 characters.",
            "Latency can trigger retries.",
        ),
        (
            "Explain cache invalidation and stale data naturally.",
            "Cache invalidation refreshes stale data.",
        ),
        ("Explain an unknown topic naturally.", "Here is a natural response."),
    ],
)
def test_natural_and_unknown_requests_are_deterministic(input_text: str, natural: str):
    baseline, _, _ = FakeModel(seed=91).generate("baseline", BASELINE_PROMPT, input_text)
    safe, _, _ = FakeModel(seed=91).generate("safe", SAFE_PROMPT, input_text)
    overfit, _, _ = FakeModel(seed=91).generate("overfit", OVERFIT_PROMPT, input_text)

    assert baseline == safe == natural
    assert json.loads(overfit) == {"answer": natural}


def test_optimizer_returns_no_candidates_without_observed_target_failures():
    optimizer = FakeOptimizer()
    all_pass = _eval_result(_case_result(
        "passed_case",
        passed=True,
        failure_category="format_violation",
    ))
    unrelated_failure = _eval_result(_case_result(
        "failed_case",
        passed=False,
        failure_category="llm_rubric_not_met",
    ))
    unclassified_failure = _eval_result(_case_result(
        "unclassified_failure",
        passed=False,
        failure_category=None,
    ))

    assert (optimizer.propose(
        BASELINE_PROMPT,
        all_pass,
        {},
    ) == [])
    assert optimizer.propose(BASELINE_PROMPT, unclassified_failure, {}) == []
    assert (optimizer.propose(
        BASELINE_PROMPT,
        unrelated_failure,
        {"by_category": {
            "llm_rubric_not_met": 1
        }},
    ) == [])


def test_optimizer_rejects_validation_result_as_training_evidence():
    baseline_validation = replace(_eval_result(), split="validation")

    with pytest.raises(ValueError, match="baseline_train.split.*train.*validation"):
        FakeOptimizer().propose(
            BASELINE_PROMPT,
            baseline_validation,
            {},
        )


def test_optimizer_rejects_validation_case_mixed_into_training_evidence():
    mixed_result = _eval_result(
        _case_result(
            "train_case",
            passed=False,
            failure_category="format_violation",
        ),
        replace(
            _case_result(
                "validation_case",
                passed=False,
                failure_category="format_violation",
            ),
            split="validation",
        ),
    )

    with pytest.raises(ValueError, match="case.*validation_case.*split.*validation"):
        FakeOptimizer().propose(
            BASELINE_PROMPT,
            mixed_result,
            {},
        )


@pytest.mark.parametrize("failure_summary", [None, [], "not-a-summary"])
def test_optimizer_rejects_non_mapping_failure_summary(failure_summary: object, ):
    observed_failure = _eval_result(
        _case_result(
            "observed_failure",
            passed=False,
            failure_category="format_violation",
        ))

    with pytest.raises(TypeError, match="^failure_summary must be a dict$"):
        FakeOptimizer().propose(
            BASELINE_PROMPT,
            observed_failure,
            failure_summary,
        )


def test_optimizer_rejects_summary_category_not_observed_in_failed_cases():
    unclassified_failure = _eval_result(_case_result(
        "unclassified_failure",
        passed=False,
        failure_category=None,
    ))

    with pytest.raises(ValueError, match="by_category.*failed train cases"):
        FakeOptimizer().propose(
            BASELINE_PROMPT,
            unclassified_failure,
            {"by_category": {
                "format_violation": 1
            }},
        )


@pytest.mark.parametrize(
    "by_category",
    [
        {
            "final_response_mismatch": 1
        },
        {
            "format_violation": 2
        },
        {},
    ],
)
def test_optimizer_rejects_summary_category_or_count_mismatch(by_category: dict[str, object], ):
    observed_failure = _eval_result(
        _case_result(
            "observed_failure",
            passed=False,
            failure_category="format_violation",
        ))

    with pytest.raises(ValueError, match="by_category.*failed train cases"):
        FakeOptimizer().propose(
            BASELINE_PROMPT,
            observed_failure,
            {"by_category": by_category},
        )


@pytest.mark.parametrize("by_category", [None, [], "format_violation"])
def test_optimizer_rejects_non_mapping_by_category(by_category: object):
    with pytest.raises(ValueError, match="failure_summary.*by_category.*dict"):
        FakeOptimizer().propose(
            BASELINE_PROMPT,
            _eval_result(),
            {"by_category": by_category},
        )


@pytest.mark.parametrize("count", [True, 0, -1, 1.0, 1.5, "1", None])
def test_optimizer_rejects_non_positive_integer_summary_counts(count: object):
    observed_failure = _eval_result(
        _case_result(
            "observed_failure",
            passed=False,
            failure_category="format_violation",
        ))

    with pytest.raises(ValueError, match="count.*positive integer"):
        FakeOptimizer().propose(
            BASELINE_PROMPT,
            observed_failure,
            {"by_category": {
                "format_violation": count
            }},
        )


@pytest.mark.parametrize(
    "failure_summary",
    [
        {},
        {
            "by_category": {
                "format_violation": 1
            }
        },
    ],
)
def test_optimizer_proposes_two_candidates_for_observed_train_format_failure(failure_summary: dict[str, object], ):
    baseline_train = _eval_result(
        _case_result(
            "random_training_case",
            passed=False,
            failure_category="format_violation",
        ))
    candidates = FakeOptimizer().propose(
        BASELINE_PROMPT,
        baseline_train,
        failure_summary,
    )

    assert [candidate.candidate_id for candidate in candidates] == [
        "candidate_001_overfit",
        "candidate_002_safe",
    ]
    assert OVERFIT_INSTRUCTION in candidates[0].prompt
    assert SAFE_INSTRUCTION in candidates[1].prompt
    assert all("OPTIMIZER_MARKER" not in candidate.prompt for candidate in candidates)
    assert all(candidate.prompt_fields == {"system_prompt": candidate.prompt} for candidate in candidates)
    assert all(candidate.rationale for candidate in candidates)
    assert all(candidate.prompt_diff for candidate in candidates)


@pytest.mark.asyncio
async def test_fake_backend_passes_training_evidence_to_optimizer(tmp_path: Path):
    backend = FakeBackend(seed=91)
    no_evidence = await backend.optimize_candidates(
        baseline_prompts={"system_prompt": BASELINE_PROMPT},
        baseline_train=_eval_result(),
        failure_summary={},
        train_path=TRAIN_PATH,
        validation_path=VALIDATION_PATH,
        config_path=tmp_path / "unused.json",
        artifact_dir=tmp_path / "unused",
    )
    with_evidence = await backend.optimize_candidates(
        baseline_prompts={"system_prompt": BASELINE_PROMPT},
        baseline_train=_eval_result(_case_result(
            "not-a-sample-id",
            passed=False,
            failure_category="format_violation",
        )),
        failure_summary={"by_category": {
            "format_violation": 1
        }},
        train_path=TRAIN_PATH,
        validation_path=VALIDATION_PATH,
        config_path=tmp_path / "unused.json",
        artifact_dir=tmp_path / "unused",
    )

    assert no_evidence.candidates == []
    assert no_evidence.rounds == []
    assert len(with_evidence.candidates) == 2
    assert len(with_evidence.rounds) == 2


def test_fake_backend_sync_optimizer_refuses_to_invent_failure_evidence(tmp_path: Path):
    with pytest.raises(RuntimeError, match="failure evidence|optimize_candidates"):
        FakeBackend(seed=91).optimize(
            baseline_prompt=BASELINE_PROMPT,
            train_path=TRAIN_PATH,
            val_path=VALIDATION_PATH,
            optimizer_config_path=tmp_path / "unused.json",
            output_dir=tmp_path / "unused",
        )


def test_example_data_is_official_sdk_evalset_with_self_contained_user_inputs():
    expected_inputs = {
        TRAIN_PATH: [
            "Return strict JSON with intent=refund and priority=high.",
            "Return strict JSON with status=READY and next_step=ship.",
            "Explain latency and retries naturally in under 80 characters.",
        ],
        VALIDATION_PATH: [
            "Return strict JSON with status=approved and next_step=email_customer.",
            "Explain cache invalidation and stale data naturally.",
            "Return only YES; do not use JSON.",
        ],
    }
    all_eval_ids: list[str] = []
    all_invocation_ids: list[str] = []

    for path, inputs in expected_inputs.items():
        raw = path.read_text(encoding="utf-8")
        validated = EvalSet.model_validate_json(raw)
        payload = json.loads(raw)
        assert [case["conversation"][-1]["user_content"]["parts"][0]["text"]
                for case in payload["eval_cases"]] == inputs
        assert len(validated.eval_cases) == 3
        all_eval_ids.extend(case.eval_id for case in validated.eval_cases)
        all_invocation_ids.extend(invocation.invocation_id for case in validated.eval_cases
                                  for invocation in case.conversation or [])

    assert len(all_eval_ids) == len(set(all_eval_ids)) == 6
    assert len(all_invocation_ids) == len(set(all_invocation_ids)) == 6


@pytest.mark.asyncio
async def test_fake_backend_scores_baseline_overfit_and_safe_on_official_data(tmp_path: Path, ):
    backend = FakeBackend(seed=91)
    prompts = {
        "baseline": BASELINE_PROMPT,
        "overfit": OVERFIT_PROMPT,
        "safe": SAFE_PROMPT,
    }
    expected = {
        ("baseline", "train"): (1 / 3, [False, False, True]),
        ("overfit", "train"): (1.0, [True, True, True]),
        ("safe", "train"): (1.0, [True, True, True]),
        ("baseline", "validation"): (2 / 3, [False, True, True]),
        ("overfit", "validation"): (1 / 3, [True, False, False]),
        ("safe", "validation"): (1.0, [True, True, True]),
    }

    for prompt_id, prompt in prompts.items():
        for split, dataset_path in (
            ("train", TRAIN_PATH),
            ("validation", VALIDATION_PATH),
        ):
            result = await backend.evaluate(
                prompt_id=prompt_id,
                prompts={"system_prompt": prompt},
                dataset_path=dataset_path,
                split=split,
                trace=True,
                artifact_dir=tmp_path / prompt_id / split,
            )
            expected_score, expected_passes = expected[(prompt_id, split)]
            assert result.score == pytest.approx(expected_score, abs=1e-6)
            assert [case.passed for case in result.cases] == expected_passes
            assert all(case.trace == {
                "seed": 91,
                "prompt_id": prompt_id,
                "prompt_mode": prompt_id,
            } for case in result.cases)


def _case_result(
    case_id: str,
    *,
    passed: bool,
    failure_category: str | None,
) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        split="train",
        score=1.0 if passed else 0.0,
        passed=passed,
        output="output",
        failure_category=failure_category,
    )


def _eval_result(*cases: CaseResult) -> EvalResult:
    score = sum(case.score for case in cases) / len(cases) if cases else 0.0
    return EvalResult(
        prompt_id="baseline",
        split="train",
        score=score,
        passed=all(case.passed for case in cases),
        cost=0.0,
        cases=list(cases),
    )
