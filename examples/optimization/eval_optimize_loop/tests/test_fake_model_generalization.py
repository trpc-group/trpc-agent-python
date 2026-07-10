from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from trpc_agent_sdk.evaluation import EvalSet

from examples.optimization.eval_optimize_loop.eval_loop.backends import FakeBackend
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


def test_model_output_and_trace_ignore_all_evaluator_only_case_fields():
    input_text = "Return strict JSON with intent=refund and priority=high."
    base = EvalCase(
        case_id="oracle_case_a",
        split="train",
        input=input_text,
        expectation={
            "type": "json",
            "expected_values": {"secret": "first-oracle"},
        },
        tags=["first-tag"],
        protected=False,
        simulated_outputs={"safe": "FIRST SIMULATED ORACLE"},
    )
    variants = [
        base,
        replace(base, case_id="oracle_case_b"),
        replace(
            base,
            expectation={"type": "exact", "expected": "SECOND ORACLE"},
        ),
        replace(base, split="validation"),
        replace(base, protected=True),
        replace(base, tags=["different", "oracle-tag"]),
        replace(base, simulated_outputs={"safe": "SECOND SIMULATED ORACLE"}),
    ]

    generated = [FakeModel(seed=91).generate("candidate", SAFE_PROMPT, case) for case in variants]

    assert {output for output, _, _ in generated} == {'{"intent": "refund", "priority": "high"}'}
    assert {json.dumps(trace, sort_keys=True) for _, trace, _ in generated} == {
        json.dumps(
            {"seed": 91, "prompt_id": "candidate", "prompt_mode": "safe"},
            sort_keys=True,
        )
    }
    serialized_traces = json.dumps([trace for _, trace, _ in generated])
    assert "ORACLE" not in serialized_traces
    assert "oracle_case" not in serialized_traces


def test_model_is_deterministic_and_prompt_id_or_seed_cannot_change_output():
    case = _case("Return strict JSON with status=READY and next_step=ship.")
    model = FakeModel(seed=91)

    first = model.generate("first-id", SAFE_PROMPT, case)
    repeated = model.generate("first-id", SAFE_PROMPT, case)
    different_prompt_id = model.generate("second-id", SAFE_PROMPT, case)
    different_seed = FakeModel(seed=999).generate("first-id", SAFE_PROMPT, case)

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
    case = _case("Return StRiCt JsOn with intent=refund and priority=high.")

    output, _, _ = FakeModel(seed=91).generate("arbitrary-id", prompt, case)

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
    case = _case("ReTuRn OnLy YES; do not use JSON.")

    output, _, _ = FakeModel(seed=91).generate("arbitrary-id", prompt, case)

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
    case = _case(input_text)

    baseline, _, _ = FakeModel(seed=91).generate("baseline", BASELINE_PROMPT, case)
    safe, _, _ = FakeModel(seed=91).generate("safe", SAFE_PROMPT, case)
    overfit, _, _ = FakeModel(seed=91).generate("overfit", OVERFIT_PROMPT, case)

    assert baseline == safe == natural
    assert json.loads(overfit) == {"answer": natural}


def test_optimizer_returns_no_candidates_without_observed_target_failures():
    optimizer = FakeOptimizer()
    all_pass = _eval_result(
        _case_result(
            "passed_case",
            passed=True,
            failure_category="format_violation",
        )
    )
    unrelated_failure = _eval_result(
        _case_result(
            "failed_case",
            passed=False,
            failure_category="llm_rubric_not_met",
        )
    )

    assert (
        optimizer.propose(
            BASELINE_PROMPT,
            all_pass,
            {"by_category": {"format_violation": 1}},
        )
        == []
    )
    assert (
        optimizer.propose(
            BASELINE_PROMPT,
            unrelated_failure,
            {"by_category": {"llm_rubric_not_met": 1}},
        )
        == []
    )


@pytest.mark.parametrize(
    ("case_failure_category", "failure_summary"),
    [
        ("format_violation", {}),
        (None, {"by_category": {"final_response_mismatch": 2}}),
    ],
)
def test_optimizer_proposes_natural_language_candidates_from_observed_failures(
    case_failure_category: str | None,
    failure_summary: dict[str, object],
):
    baseline_train = _eval_result(
        _case_result(
            "random_training_case",
            passed=False,
            failure_category=case_failure_category,
        )
    )
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
        baseline_train=_eval_result(
            _case_result(
                "not-a-sample-id",
                passed=False,
                failure_category="format_violation",
            )
        ),
        failure_summary={"by_category": {"format_violation": 1}},
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
        assert [
            case["conversation"][-1]["user_content"]["parts"][0]["text"] for case in payload["eval_cases"]
        ] == inputs
        assert len(validated.eval_cases) == 3
        all_eval_ids.extend(case.eval_id for case in validated.eval_cases)
        all_invocation_ids.extend(
            invocation.invocation_id for case in validated.eval_cases for invocation in case.conversation or []
        )

    assert len(all_eval_ids) == len(set(all_eval_ids)) == 6
    assert len(all_invocation_ids) == len(set(all_invocation_ids)) == 6


@pytest.mark.asyncio
async def test_fake_backend_scores_baseline_overfit_and_safe_on_official_data(
    tmp_path: Path,
):
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
            assert all(
                case.trace
                == {
                    "seed": 91,
                    "prompt_id": prompt_id,
                    "prompt_mode": prompt_id,
                }
                for case in result.cases
            )


def _case(input_text: str) -> EvalCase:
    return EvalCase(
        case_id="arbitrary-case-id",
        split="train",
        input=input_text,
        expectation={"type": "exact", "expected": "EVALUATOR ORACLE"},
    )


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
