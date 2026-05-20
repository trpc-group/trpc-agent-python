# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for _AgentGEPACallback buffering iteration events as RoundRecords."""

from __future__ import annotations

from trpc_agent_sdk.evaluation._optimize_gepa_callback import _AgentGEPACallback
from trpc_agent_sdk.evaluation._optimize_gepa_callback import _translate_skip_reason


def _trigger_iteration(
    callback: _AgentGEPACallback,
    *,
    iteration: int,
    candidate: dict,
    average_score: float,
    is_best: bool = False,
    proposal_accepted: bool = False,
) -> None:
    callback.on_iteration_start({"iteration": iteration, "state": None, "trainset_loader": None})
    callback.on_valset_evaluated(
        {
            "iteration": iteration,
            "candidate_idx": iteration,
            "candidate": candidate,
            "scores_by_val_id": {},
            "average_score": average_score,
            "num_examples_evaluated": 1,
            "total_valset_size": 1,
            "parent_ids": [],
            "is_best_program": is_best,
            "outputs_by_val_id": None,
        }
    )
    callback.on_iteration_end(
        {"iteration": iteration, "state": None, "proposal_accepted": proposal_accepted}
    )


def test_callback_starts_with_empty_buffer():
    callback = _AgentGEPACallback()
    assert callback.rounds == []
    assert callback.baseline_metric_breakdown == {}
    assert callback.baseline_failed_case_ids == []
    assert callback.baseline_pass_rate == 0.0


def test_callback_captures_seed_evaluation_into_baseline_not_rounds():
    """gepa emits ``iteration == 0`` exactly once for the seed candidate.
    Callback must store it as baseline rather than appending a RoundRecord."""

    class _StubOutcome:
        metric_breakdown = {"final_response_avg_score": 0.42}
        failed_case_ids = ["case-2"]

    class _StubAdapter:
        last_outcome = _StubOutcome()

    callback = _AgentGEPACallback(adapter=_StubAdapter())
    callback.on_valset_evaluated(
        {
            "iteration": 0,
            "candidate_idx": 0,
            "candidate": {"instruction": "baseline"},
            "scores_by_val_id": {},
            "average_score": 0.42,
            "num_examples_evaluated": 1,
            "total_valset_size": 1,
            "parent_ids": [],
            "is_best_program": True,
            "outputs_by_val_id": None,
        }
    )

    assert callback.rounds == []
    assert callback.baseline_metric_breakdown == {"final_response_avg_score": 0.42}
    assert callback.baseline_failed_case_ids == ["case-2"]
    assert callback.baseline_pass_rate == 0.42


def test_callback_records_one_round_per_iteration():
    callback = _AgentGEPACallback()
    _trigger_iteration(
        callback,
        iteration=1,
        candidate={"instruction": "v1"},
        average_score=0.6,
        is_best=False,
        proposal_accepted=False,
    )
    _trigger_iteration(
        callback,
        iteration=2,
        candidate={"instruction": "v2"},
        average_score=0.9,
        is_best=True,
        proposal_accepted=True,
    )

    assert len(callback.rounds) == 2
    assert callback.rounds[0].round == 1
    assert callback.rounds[0].validation_pass_rate == 0.6
    assert callback.rounds[0].candidate_prompts == {"instruction": "v1"}
    assert callback.rounds[0].accepted is False

    assert callback.rounds[1].round == 2
    assert callback.rounds[1].validation_pass_rate == 0.9
    assert callback.rounds[1].candidate_prompts == {"instruction": "v2"}
    assert callback.rounds[1].accepted is True


def test_callback_acceptance_via_proposal_accepted_only():
    """proposal_accepted=True alone should mark the round accepted."""
    callback = _AgentGEPACallback()
    _trigger_iteration(
        callback,
        iteration=1,
        candidate={"instruction": "v1"},
        average_score=0.7,
        is_best=False,
        proposal_accepted=True,
    )
    assert callback.rounds[0].accepted is True


def test_callback_acceptance_follows_proposal_accepted_only():
    """A candidate flagged is_best_program=True without proposal_accepted=True
    must not be reported as accepted: the user-facing "accepted" status follows
    gepa's proposal_accepted contract so the timeline matches gepa's own
    acceptance log.
    """
    callback = _AgentGEPACallback()
    _trigger_iteration(
        callback,
        iteration=1,
        candidate={"instruction": "v1"},
        average_score=0.7,
        is_best=True,
        proposal_accepted=False,
    )
    assert callback.rounds[0].accepted is False


def test_callback_always_emits_record_even_when_valset_not_evaluated():
    """Iterations rejected by the subsample gate still get a RoundRecord so
    round indices in the reporter stay contiguous with gepa iterations.
    """
    callback = _AgentGEPACallback()
    callback.on_iteration_start({"iteration": 1, "state": None, "trainset_loader": None})
    callback.on_iteration_end({"iteration": 1, "state": None, "proposal_accepted": False})
    assert len(callback.rounds) == 1
    record = callback.rounds[0]
    assert record.round == 1
    assert record.skip_reason == "reflect-LM produced no usable new prompt"
    assert record.candidate_prompts == {}
    assert record.accepted is False


def test_callback_records_candidate_field_names_falls_back_to_candidate_keys():
    """Without an ``on_proposal_end`` event (e.g. merge round, or any path
    that bypasses the reflective proposer), ``optimized_field_names``
    falls back to the full candidate key set so result.json never
    surfaces an empty list when a candidate exists.
    """
    callback = _AgentGEPACallback()
    _trigger_iteration(
        callback,
        iteration=1,
        candidate={"system": "s1", "react": "r1"},
        average_score=0.5,
    )
    assert set(callback.rounds[0].optimized_field_names) == {"system", "react"}


def test_callback_optimized_field_names_uses_proposal_end_components_only():
    """F-3: when ``on_proposal_end`` fires, ``optimized_field_names`` must
    reflect ONLY the components rewritten by the reflection LM this
    round (gepa's RoundRobin / random component selectors mutate a
    subset of the candidate's components per iteration).

    Previously the field reported the full ``candidate.keys()`` list,
    misleading users into thinking every component was rewritten each
    round when only one (or a subset) actually was.
    """
    callback = _AgentGEPACallback()
    callback.on_iteration_start({"iteration": 1, "state": None, "trainset_loader": None})
    # gepa's RoundRobin selector picked only "dim_intent" this round; the
    # reflection LM produced exactly one new instruction.
    callback.on_proposal_end(
        {
            "iteration": 1,
            "new_instructions": {"dim_intent": "rewritten intent prompt"},
            "subsample_scores_before": [0.5],
            "subsample_scores_after": [0.7],
        }
    )
    callback.on_valset_evaluated(
        {
            "iteration": 1,
            "candidate": {
                "system": "s1",
                "dim_intent": "rewritten intent prompt",
                "dim_slot": "s2",
                "dim_response": "r1",
                "dim_summary": "su1",
            },
            "average_score": 0.7,
            "is_best_program": False,
        }
    )
    callback.on_iteration_end({"iteration": 1, "state": None, "proposal_accepted": True})

    assert callback.rounds[0].optimized_field_names == ["dim_intent"]
    # candidate_prompts still carries the full candidate (used elsewhere
    # for ``best_prompts`` etc.); only the "what was changed this round"
    # metadata is narrowed.
    assert set(callback.rounds[0].candidate_prompts.keys()) == {
        "system",
        "dim_intent",
        "dim_slot",
        "dim_response",
        "dim_summary",
    }


def test_callback_optimized_field_names_resets_between_iterations():
    """``_iter_changed_components`` must reset on ``on_iteration_start``;
    a proposal event in iteration N must not leak into iteration N+1's
    ``optimized_field_names`` when the next iteration has no proposal
    event of its own (e.g. a merge round following a reflective round).
    """
    callback = _AgentGEPACallback()

    # Iteration 1: reflective round, only "dim_intent" rewritten.
    callback.on_iteration_start({"iteration": 1, "state": None, "trainset_loader": None})
    callback.on_proposal_end(
        {"iteration": 1, "new_instructions": {"dim_intent": "v1"}}
    )
    callback.on_valset_evaluated(
        {
            "iteration": 1,
            "candidate": {"dim_intent": "v1", "dim_slot": "s0"},
            "average_score": 0.6,
            "is_best_program": False,
        }
    )
    callback.on_iteration_end({"iteration": 1, "state": None, "proposal_accepted": True})

    # Iteration 2: merge round — no on_proposal_end, must fall back to
    # full candidate keys, NOT reuse iteration 1's ["dim_intent"].
    callback.on_iteration_start({"iteration": 2, "state": None, "trainset_loader": None})
    callback.on_merge_attempted({"iteration": 2})
    callback.on_valset_evaluated(
        {
            "iteration": 2,
            "candidate": {"dim_intent": "v1", "dim_slot": "s0"},
            "average_score": 0.65,
            "is_best_program": False,
        }
    )
    callback.on_iteration_end({"iteration": 2, "state": None, "proposal_accepted": True})

    assert callback.rounds[0].optimized_field_names == ["dim_intent"]
    assert set(callback.rounds[1].optimized_field_names) == {"dim_intent", "dim_slot"}
    assert callback.rounds[1].kind == "merge"


# ---------------------------------------------------------------------------
# on_evaluation_end: parent / candidate subsample-score routing (F-5)
# ---------------------------------------------------------------------------
#
# gepa marks the post-mutation / post-merge eval with ``candidate_idx=None``;
# every other evaluation_end carries an int ``candidate_idx`` and represents
# the parent / current-program eval. Earlier seq-based logic misclassified
# rounds where the reflective proposer picked the seed program (id=0) as
# parent because gepa flags that parent eval with ``is_seed_candidate=True``.


def test_on_evaluation_end_records_parent_then_candidate_normal_round():
    """Normal reflective round: parent eval first (int idx), then new
    candidate eval (idx=None). Both scores must land on the right slots.
    """
    callback = _AgentGEPACallback()
    callback.on_iteration_start({"iteration": 2, "state": None, "trainset_loader": None})
    # Parent eval (curr_prog_id=5, NOT seed). Use scores that average
    # exactly to 0.5 so the assertion is float-safe.
    callback.on_evaluation_end(
        {
            "iteration": 2,
            "candidate_idx": 5,
            "scores": [0.4, 0.5, 0.6],
            "is_seed_candidate": False,
        }
    )
    # New candidate eval (post-mutation, candidate_idx=None).
    callback.on_evaluation_end(
        {
            "iteration": 2,
            "candidate_idx": None,
            "scores": [0.8, 0.9, 1.0],
            "is_seed_candidate": False,
        }
    )
    assert callback._iter_train_parent_score == 0.5  # noqa: SLF001
    assert callback._iter_train_candidate_score == 0.9  # noqa: SLF001
    assert callback._iter_train_minibatch_size == 3  # noqa: SLF001


def test_on_evaluation_end_records_correctly_when_parent_is_seed():
    """F-5 regression: when reflective_mutation picks the seed program
    (id=0) as parent, the parent eval is flagged ``is_seed_candidate=True``.
    Earlier logic dropped that event and shifted the candidate score
    into the parent slot — verify the new ``candidate_idx``-based routing
    keeps the slots correct.
    """
    callback = _AgentGEPACallback()
    callback.on_iteration_start({"iteration": 1, "state": None, "trainset_loader": None})
    # Parent eval — parent IS the seed program. gepa sets is_seed_candidate=True
    # here (reflective_mutation.py:283).
    callback.on_evaluation_end(
        {
            "iteration": 1,
            "candidate_idx": 0,
            "scores": [0.5, 0.5],
            "is_seed_candidate": True,
        }
    )
    # New candidate eval.
    callback.on_evaluation_end(
        {
            "iteration": 1,
            "candidate_idx": None,
            "scores": [0.9, 0.9],
            "is_seed_candidate": False,
        }
    )
    # Parent slot must carry the seed score, NOT the candidate score.
    assert callback._iter_train_parent_score == 0.5  # noqa: SLF001
    assert callback._iter_train_candidate_score == 0.9  # noqa: SLF001


def test_on_evaluation_end_merge_round_only_candidate_score():
    """Merge round emits exactly one evaluation_end with ``candidate_idx=None``
    (merge.py:376). Parent slot must stay None — merge has two parents,
    a single ``parent_score`` doesn't apply.
    """
    callback = _AgentGEPACallback()
    callback.on_iteration_start({"iteration": 3, "state": None, "trainset_loader": None})
    callback.on_merge_attempted({"iteration": 3})
    callback.on_evaluation_end(
        {
            "iteration": 3,
            "candidate_idx": None,
            "scores": [0.7, 0.7, 0.7, 0.7],
            "is_seed_candidate": False,
        }
    )
    assert callback._iter_train_parent_score is None  # noqa: SLF001
    assert callback._iter_train_candidate_score == 0.7  # noqa: SLF001


def test_on_evaluation_end_skips_empty_scores():
    """Empty scores carry no information — leave both slots untouched."""
    callback = _AgentGEPACallback()
    callback.on_iteration_start({"iteration": 1, "state": None, "trainset_loader": None})
    callback.on_evaluation_end(
        {
            "iteration": 1,
            "candidate_idx": 5,
            "scores": [],
            "is_seed_candidate": False,
        }
    )
    callback.on_evaluation_end(
        {
            "iteration": 1,
            "candidate_idx": None,
            "scores": None,
            "is_seed_candidate": False,
        }
    )
    assert callback._iter_train_parent_score is None  # noqa: SLF001
    assert callback._iter_train_candidate_score is None  # noqa: SLF001


def test_on_evaluation_end_minibatch_size_set_from_parent_when_unset():
    """When ``on_minibatch_sampled`` did not fire (or fired with empty
    list), the parent eval's ``len(scores)`` is the next-best signal for
    the round's minibatch size.
    """
    callback = _AgentGEPACallback()
    callback.on_iteration_start({"iteration": 1, "state": None, "trainset_loader": None})
    # No on_minibatch_sampled — minibatch size starts at 0.
    assert callback._iter_train_minibatch_size == 0  # noqa: SLF001
    callback.on_evaluation_end(
        {
            "iteration": 1,
            "candidate_idx": 5,
            "scores": [0.4, 0.5, 0.6],  # 3 cases
            "is_seed_candidate": False,
        }
    )
    assert callback._iter_train_minibatch_size == 3  # noqa: SLF001


def test_on_evaluation_end_does_not_overwrite_minibatch_size_from_sampled():
    """If ``on_minibatch_sampled`` already set the minibatch size,
    parent eval's score count must NOT clobber it (the sampled event is
    authoritative — it counts the FULL minibatch even when the eval
    short-circuits a subset).
    """
    callback = _AgentGEPACallback()
    callback.on_iteration_start({"iteration": 1, "state": None, "trainset_loader": None})
    callback.on_minibatch_sampled(
        {"iteration": 1, "minibatch_ids": ["a", "b", "c", "d", "e"], "trainset_size": 5}
    )
    assert callback._iter_train_minibatch_size == 5  # noqa: SLF001
    # Parent eval somehow only scored 2 cases — minibatch_size stays 5.
    callback.on_evaluation_end(
        {
            "iteration": 1,
            "candidate_idx": 5,
            "scores": [0.4, 0.5],
            "is_seed_candidate": False,
        }
    )
    assert callback._iter_train_minibatch_size == 5  # noqa: SLF001


def test_callback_records_duration_seconds_non_negative():
    callback = _AgentGEPACallback()
    _trigger_iteration(
        callback,
        iteration=1,
        candidate={"instruction": "v1"},
        average_score=0.5,
    )
    assert callback.rounds[0].duration_seconds >= 0.0


def test_callback_reasoning_includes_score():
    callback = _AgentGEPACallback()
    _trigger_iteration(
        callback,
        iteration=1,
        candidate={"instruction": "v1"},
        average_score=0.8765,
        is_best=True,
    )
    assert "0.8765" in callback.rounds[0].acceptance_reason


class _FakeOutcome:
    def __init__(self, metric_breakdown: dict, failed_case_ids: list) -> None:
        self.metric_breakdown = metric_breakdown
        self.failed_case_ids = failed_case_ids


class _FakeAdapter:
    def __init__(self, outcome: _FakeOutcome) -> None:
        self.last_outcome = outcome


class _FakeReflectionLM:
    def __init__(self) -> None:
        self.total_calls = 0
        self.total_cost = 0.0
        self.total_token_usage = {"prompt": 0, "completion": 0, "total": 0}

    def make_call(self, prompt_tokens: int = 10, completion_tokens: int = 5, cost: float = 0.01) -> None:
        self.total_calls += 1
        self.total_cost += cost
        self.total_token_usage["prompt"] += prompt_tokens
        self.total_token_usage["completion"] += completion_tokens
        self.total_token_usage["total"] += prompt_tokens + completion_tokens


def test_callback_pulls_metric_breakdown_and_failures_from_adapter():
    """B2: when adapter is supplied, callback fills metric_breakdown / failed_case_ids."""
    outcome = _FakeOutcome(
        metric_breakdown={"m1": 0.7, "m2": 0.4},
        failed_case_ids=["c3", "c5"],
    )
    adapter = _FakeAdapter(outcome)
    callback = _AgentGEPACallback(adapter=adapter)
    _trigger_iteration(
        callback,
        iteration=1,
        candidate={"instruction": "v1"},
        average_score=0.55,
    )
    assert callback.rounds[0].metric_breakdown == {"m1": 0.7, "m2": 0.4}
    assert callback.rounds[0].failed_case_ids == ["c3", "c5"]


def test_callback_records_per_round_reflection_lm_call_delta():
    """Reflection-LM calls/cost/tokens between iteration_start and iteration_end
    should land on the produced RoundRecord."""
    lm = _FakeReflectionLM()
    callback = _AgentGEPACallback(reflection_lm=lm)

    callback.on_iteration_start({"iteration": 1, "state": None, "trainset_loader": None})
    lm.make_call(prompt_tokens=20, completion_tokens=10, cost=0.02)
    lm.make_call(prompt_tokens=15, completion_tokens=8, cost=0.015)
    callback.on_valset_evaluated(
        {
            "iteration": 1,
            "candidate": {"instruction": "v1"},
            "average_score": 0.7,
            "is_best_program": False,
        }
    )
    callback.on_iteration_end({"iteration": 1, "state": None, "proposal_accepted": False})

    record = callback.rounds[0]
    assert record.reflection_lm_calls == 2
    assert record.round_llm_cost == 0.035
    assert record.round_token_usage == {"prompt": 35, "completion": 18, "total": 53}


def test_translate_skip_reason_handles_known_strings():
    assert (
        _translate_skip_reason("no_trajectories")
        == "no trajectories captured this round"
    )
    assert (
        _translate_skip_reason("all_scores_perfect")
        == "minibatch already perfect (skip_perfect_score on)"
    )
    # Whitespace / case / dash normalisation.
    assert (
        _translate_skip_reason("All-Scores-Perfect")
        == "minibatch already perfect (skip_perfect_score on)"
    )
    assert (
        _translate_skip_reason("  no_trajectories  ")
        == "no trajectories captured this round"
    )


def test_translate_skip_reason_surfaces_unknown_strings_under_prefix():
    translated = _translate_skip_reason("some_brand_new_reason")
    assert translated is not None
    assert translated.startswith("gepa-internal:")
    assert "some_brand_new_reason" in translated


def test_translate_skip_reason_returns_none_for_empty_or_missing():
    assert _translate_skip_reason(None) is None
    assert _translate_skip_reason("") is None
    assert _translate_skip_reason("   ") is None


def test_callback_translates_skip_reason_via_on_evaluation_skipped():
    callback = _AgentGEPACallback()
    callback.on_iteration_start({"iteration": 1, "state": None, "trainset_loader": None})
    callback.on_evaluation_skipped({"reason": "all_scores_perfect"})
    callback.on_iteration_end({"iteration": 1, "state": None, "proposal_accepted": False})
    assert (
        callback.rounds[0].skip_reason
        == "minibatch already perfect (skip_perfect_score on)"
    )


def test_callback_translates_no_trajectories_skip_reason():
    callback = _AgentGEPACallback()
    callback.on_iteration_start({"iteration": 1, "state": None, "trainset_loader": None})
    callback.on_evaluation_skipped({"reason": "no_trajectories"})
    callback.on_iteration_end({"iteration": 1, "state": None, "proposal_accepted": False})
    assert callback.rounds[0].skip_reason == "no trajectories captured this round"


def test_callback_uses_no_proposal_fallback_when_no_event_observed():
    callback = _AgentGEPACallback()
    callback.on_iteration_start({"iteration": 1, "state": None, "trainset_loader": None})
    callback.on_iteration_end({"iteration": 1, "state": None, "proposal_accepted": False})
    assert callback.rounds[0].skip_reason == "reflect-LM produced no usable new prompt"


# ---------------------------------------------------------------------------
# on_valset_breakdown plumb-through (Framework stop policy)
# ---------------------------------------------------------------------------


def test_callback_invokes_on_valset_breakdown_for_candidate_iteration():
    received: list[dict] = []
    outcome = _FakeOutcome(
        metric_breakdown={"m1": 0.6, "m2": 0.4},
        failed_case_ids=[],
    )
    callback = _AgentGEPACallback(
        adapter=_FakeAdapter(outcome),
        on_valset_breakdown=lambda bd: received.append(bd),
    )
    _trigger_iteration(
        callback,
        iteration=1,
        candidate={"instruction": "v1"},
        average_score=0.5,
    )
    assert received == [{"m1": 0.6, "m2": 0.4}]


def test_callback_invokes_on_valset_breakdown_for_baseline_iteration():
    received: list[dict] = []
    outcome = _FakeOutcome(
        metric_breakdown={"m1": 0.7},
        failed_case_ids=[],
    )
    callback = _AgentGEPACallback(
        adapter=_FakeAdapter(outcome),
        on_valset_breakdown=lambda bd: received.append(bd),
    )
    callback.on_valset_evaluated(
        {
            "iteration": 0,
            "candidate": {"instruction": "baseline"},
            "average_score": 0.7,
            "is_best_program": True,
        }
    )
    assert received == [{"m1": 0.7}]


def test_callback_does_not_invoke_on_valset_breakdown_for_skip_without_eval():
    """Iterations that skip valset evaluation (e.g. subsample gate rejected the
    candidate) must not push stale breakdowns to the stopper."""
    received: list[dict] = []
    callback = _AgentGEPACallback(
        on_valset_breakdown=lambda bd: received.append(bd),
    )
    callback.on_iteration_start({"iteration": 1, "state": None, "trainset_loader": None})
    callback.on_evaluation_skipped({"reason": "all_scores_perfect"})
    callback.on_iteration_end({"iteration": 1, "state": None, "proposal_accepted": False})
    assert received == []


def test_callback_on_valset_breakdown_none_is_safe_default():
    callback = _AgentGEPACallback()
    _trigger_iteration(
        callback,
        iteration=1,
        candidate={"instruction": "v1"},
        average_score=0.5,
    )
    assert callback.rounds[0].validation_pass_rate == 0.5


def test_callback_on_valset_breakdown_exceptions_do_not_break_loop():
    def _boom(_bd):
        raise RuntimeError("boom")

    outcome = _FakeOutcome(metric_breakdown={"m1": 0.5}, failed_case_ids=[])
    callback = _AgentGEPACallback(
        adapter=_FakeAdapter(outcome),
        on_valset_breakdown=_boom,
    )
    _trigger_iteration(
        callback,
        iteration=1,
        candidate={"instruction": "v1"},
        average_score=0.5,
    )
    assert len(callback.rounds) == 1
