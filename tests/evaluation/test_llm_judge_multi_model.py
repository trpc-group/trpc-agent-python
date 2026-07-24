# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end multi-model evaluation tests for LLMJudge (mocked judge agents)."""

from unittest.mock import patch

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import EvalMetric
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import Invocation
from trpc_agent_sdk.evaluation._llm_judge import LLMJudge

# Module-level dict configuring per-model stubbed outcomes:
#   "valid"   -> JSON judge response with verdict valid
#   "invalid" -> JSON judge response with verdict invalid
#   "raise"   -> raise RuntimeError on get_response
_STUB_RESPONSES: dict[str, str] = {}


class _StubModel:
    """Tag object returned from stubbed _create_judge_model."""

    def __init__(self, name: str) -> None:
        self._stub_name = name


def _stub_create_judge_model(opts):
    return _StubModel(opts.model_name or "")


class _StubJudgeAgent:
    """Stub _JudgeAgent: returns configured JSON per call (for llm_final_response)."""

    def __init__(self, model, config, system_prompt, output_schema=None, tools=None, planner=None):
        self._model_name = getattr(model, "_stub_name", "")
        self._planner = planner

    async def get_response(self, user_message: str) -> str:
        outcome = _STUB_RESPONSES.get(self._model_name, "valid")
        if outcome == "raise":
            raise RuntimeError(f"stubbed judge {self._model_name} failure")
        verdict = "valid" if outcome == "valid" else "invalid"
        return ('{"reasoning":"stub","is_the_agent_response_valid":'
                f'"{verdict}"'
                "}")


@pytest.fixture(autouse=True)
def _reset_stubs():
    _STUB_RESPONSES.clear()
    yield
    _STUB_RESPONSES.clear()


def _patch_judge_internals():
    """Return list of started patchers; caller must stop them."""
    patchers = [
        patch("trpc_agent_sdk.evaluation._llm_judge._create_judge_model", side_effect=_stub_create_judge_model),
        patch("trpc_agent_sdk.evaluation._llm_judge._JudgeAgent", _StubJudgeAgent),
    ]
    for p in patchers:
        p.start()
    return patchers


def _stop(patchers):
    for p in patchers:
        p.stop()


def _make_metric(judge_models, models_aggregator="all_pass", parallel=True, threshold=0.5):
    return EvalMetric(
        metric_name="llm_final_response",
        threshold=threshold,
        criterion={
            "llm_judge": {
                "judge_models": judge_models,
                "models_aggregator": models_aggregator,
                "parallel": parallel,
            },
        },
    )


def _make_invocation(user_text: str, response_text: str) -> Invocation:
    from trpc_agent_sdk.types import Content
    from trpc_agent_sdk.types import Part

    return Invocation(
        invocation_id="inv",
        user_content=Content(role="user", parts=[Part.from_text(text=user_text)]),
        final_response=Content(role="model", parts=[Part.from_text(text=response_text)]),
    )


class TestMultiModelAllPass:

    @pytest.mark.asyncio
    async def test_both_valid_passes(self):
        _STUB_RESPONSES.update({"glm-4.7": "valid", "gpt-4o": "valid"})
        metric = _make_metric([
            {
                "model_name": "glm-4.7"
            },
            {
                "model_name": "gpt-4o"
            },
        ], models_aggregator="all_pass")
        actual = _make_invocation("u", "a")
        expected = _make_invocation("u", "a")
        ps = _patch_judge_internals()
        try:
            judge = LLMJudge(metric)
            result = await judge.evaluate([actual], [expected])
        finally:
            _stop(ps)
        assert result.overall_eval_status == EvalStatus.PASSED
        per = result.per_invocation_results[0]
        assert per.eval_status == EvalStatus.PASSED
        assert per.per_model_scores is not None
        assert len(per.per_model_scores) == 2

    @pytest.mark.asyncio
    async def test_one_invalid_fails(self):
        _STUB_RESPONSES.update({"glm-4.7": "valid", "gpt-4o": "invalid"})
        metric = _make_metric([
            {
                "model_name": "glm-4.7"
            },
            {
                "model_name": "gpt-4o"
            },
        ], models_aggregator="all_pass")
        actual = _make_invocation("u", "a")
        expected = _make_invocation("u", "a")
        ps = _patch_judge_internals()
        try:
            judge = LLMJudge(metric)
            result = await judge.evaluate([actual], [expected])
        finally:
            _stop(ps)
        assert result.overall_eval_status == EvalStatus.FAILED
        per = result.per_invocation_results[0]
        assert per.per_model_scores is not None
        names = [m.model_name for m in per.per_model_scores]
        assert "glm-4.7" in names and "gpt-4o" in names
        gpt_entry = [m for m in per.per_model_scores if m.model_name == "gpt-4o"][0]
        assert gpt_entry.passed is False


class TestMultiModelAnyPass:

    @pytest.mark.asyncio
    async def test_one_valid_passes(self):
        _STUB_RESPONSES.update({"glm-4.7": "invalid", "gpt-4o": "valid"})
        metric = _make_metric([
            {
                "model_name": "glm-4.7"
            },
            {
                "model_name": "gpt-4o"
            },
        ], models_aggregator="any_pass")
        actual = _make_invocation("u", "a")
        expected = _make_invocation("u", "a")
        ps = _patch_judge_internals()
        try:
            judge = LLMJudge(metric)
            result = await judge.evaluate([actual], [expected])
        finally:
            _stop(ps)
        assert result.overall_eval_status == EvalStatus.PASSED


class TestMultiModelParallelEqualsSerial:

    @pytest.mark.asyncio
    async def test_parallel_same_as_serial(self):
        _STUB_RESPONSES.update({"a": "valid", "b": "invalid"})

        async def run_with(parallel):
            metric = _make_metric([
                {
                    "model_name": "a"
                },
                {
                    "model_name": "b"
                },
            ],
                                  models_aggregator="all_pass",
                                  parallel=parallel)
            actual = _make_invocation("u", "x")
            expected = _make_invocation("u", "x")
            ps = _patch_judge_internals()
            try:
                j = LLMJudge(metric)
                return await j.evaluate([actual], [expected])
            finally:
                _stop(ps)

        r_p = await run_with(True)
        r_s = await run_with(False)
        assert r_p.overall_eval_status == r_s.overall_eval_status
        assert r_p.overall_score == r_s.overall_score
        names_p = sorted(m.model_name for m in r_p.per_invocation_results[0].per_model_scores)
        names_s = sorted(m.model_name for m in r_s.per_invocation_results[0].per_model_scores)
        assert names_p == names_s


class TestMultiModelSoftFailure:

    @pytest.mark.asyncio
    async def test_one_model_raises_counts_as_fail_vote(self):
        _STUB_RESPONSES.update({"a": "valid", "b": "raise"})
        metric = _make_metric([
            {
                "model_name": "a"
            },
            {
                "model_name": "b"
            },
        ], models_aggregator="all_pass")
        actual = _make_invocation("u", "x")
        expected = _make_invocation("u", "x")
        ps = _patch_judge_internals()
        try:
            j = LLMJudge(metric)
            r = await j.evaluate([actual], [expected])
        finally:
            _stop(ps)
        assert r.overall_eval_status == EvalStatus.FAILED
        per = r.per_invocation_results[0]
        b_entry = [m for m in per.per_model_scores if m.model_name == "b"][0]
        assert b_entry.passed is False
        assert b_entry.score == 0.0
        assert "stubbed judge b failure" in b_entry.reason

    @pytest.mark.asyncio
    async def test_all_models_raise_returns_not_evaluated(self):
        _STUB_RESPONSES.update({"a": "raise", "b": "raise"})
        metric = _make_metric([
            {
                "model_name": "a"
            },
            {
                "model_name": "b"
            },
        ], models_aggregator="all_pass")
        actual = _make_invocation("u", "x")
        expected = _make_invocation("u", "x")
        ps = _patch_judge_internals()
        try:
            j = LLMJudge(metric)
            r = await j.evaluate([actual], [expected])
        finally:
            _stop(ps)
        assert r.per_invocation_results[0].eval_status == EvalStatus.NOT_EVALUATED


class TestLegacySingleModelStillWorks:

    @pytest.mark.asyncio
    async def test_legacy_single_judge_model(self):
        _STUB_RESPONSES.update({"glm-4.7": "valid"})
        metric = EvalMetric(
            metric_name="llm_final_response",
            threshold=0.5,
            criterion={
                "llm_judge": {
                    "judge_model": {
                        "model_name": "glm-4.7"
                    },
                },
            },
        )
        actual = _make_invocation("u", "x")
        expected = _make_invocation("u", "x")
        ps = _patch_judge_internals()
        try:
            j = LLMJudge(metric)
            r = await j.evaluate([actual], [expected])
        finally:
            _stop(ps)
        assert r.overall_eval_status == EvalStatus.PASSED


class TestJudgeModelResponseFormatCapabilities:

    @staticmethod
    def _capture_judge_agent_construction(metric):
        captured = []

        class CapturingJudgeAgent:

            def __init__(self, model, config, system_prompt, output_schema=None, tools=None, planner=None):
                captured.append({
                    "model": model,
                    "config": config,
                    "output_schema": output_schema,
                })

        with patch("trpc_agent_sdk.evaluation._llm_judge._JudgeAgent", CapturingJudgeAgent):
            LLMJudge(metric)
        return captured

    @staticmethod
    def _rubric_metric(model_name):
        return EvalMetric(
            metric_name="llm_rubric_response",
            threshold=0.5,
            criterion={
                "llm_judge": {
                    "judge_model": {
                        "model_name": model_name,
                    },
                },
            },
        )

    def test_deepseek_judge_uses_json_mode_without_native_schema(self):
        from trpc_agent_sdk.models.openai_adapter import _deepseek

        captured = self._capture_judge_agent_construction(self._rubric_metric("deepseek-v4-flash"))

        assert len(captured) == 1
        judge = captured[0]
        assert judge["output_schema"] is None
        assert judge["config"].response_mime_type == "application/json"
        with patch.object(_deepseek.logger, "warning") as warning:
            response_format = judge["model"]._build_response_format(judge["config"])
        assert response_format == {"type": "json_object"}
        warning.assert_not_called()

    def test_schema_capable_judge_keeps_native_schema(self):
        captured = self._capture_judge_agent_construction(self._rubric_metric("gpt-4o"))

        assert len(captured) == 1
        judge = captured[0]
        assert judge["output_schema"] is not None
        assert judge["config"].response_mime_type is None


class TestUnknownAggregatorRaisesAtConstruction:

    def test_unknown_aggregator_raises(self):
        metric = EvalMetric(
            metric_name="llm_final_response",
            threshold=0.5,
            criterion={
                "llm_judge": {
                    "judge_models": [{
                        "model_name": "a"
                    }],
                    "models_aggregator": "definitely_not_registered",
                },
            },
        )
        ps = _patch_judge_internals()
        try:
            with pytest.raises(ValueError, match="definitely_not_registered"):
                LLMJudge(metric)
        finally:
            _stop(ps)


class TestRegistryRegisteredAggregator:
    """Verify that a registry-registered ModelsAggregator is picked up by _judge_for_metric."""

    @pytest.mark.asyncio
    async def test_registered_custom_aggregator_used(self):
        """Test register_models_aggregator -> _judge_for_metric injects it; criterion name ignored."""
        from trpc_agent_sdk.evaluation import LLM_EVALUATOR_REGISTRY
        from trpc_agent_sdk.evaluation import ScoreResult
        from trpc_agent_sdk.evaluation._llm_evaluator import _judge_for_metric

        _STUB_RESPONSES.update({"a": "invalid", "b": "invalid"})

        def always_pass(per_model, threshold, weights):
            return ScoreResult(score=1.0, reason="custom always pass")

        LLM_EVALUATOR_REGISTRY.register_models_aggregator("llm_final_response", always_pass)
        try:
            metric = _make_metric(
                [
                    {
                        "model_name": "a"
                    },
                    {
                        "model_name": "b"
                    },
                ],
                models_aggregator="all_pass",
            )
            actual = _make_invocation("u", "x")
            expected = _make_invocation("u", "x")
            ps = _patch_judge_internals()
            try:
                judge = _judge_for_metric(metric)
                r = await judge.evaluate([actual], [expected])
            finally:
                _stop(ps)
            assert r.overall_eval_status == EvalStatus.PASSED
        finally:
            LLM_EVALUATOR_REGISTRY.unregister_models_aggregator("llm_final_response")
