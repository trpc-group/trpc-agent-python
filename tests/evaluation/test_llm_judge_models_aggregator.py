# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for built-in ModelsAggregator strategies in _llm_judge."""

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import ScoreResult
from trpc_agent_sdk.evaluation._llm_judge import AllPassModelsAggregator
from trpc_agent_sdk.evaluation._llm_judge import AnyPassModelsAggregator
from trpc_agent_sdk.evaluation._llm_judge import AverageModelsAggregator
from trpc_agent_sdk.evaluation._llm_judge import MajorityPassModelsAggregator
from trpc_agent_sdk.evaluation._llm_judge import WeightedAverageModelsAggregator
from trpc_agent_sdk.evaluation._llm_judge import WeightedMajorityModelsAggregator
from trpc_agent_sdk.evaluation._llm_judge import get_builtin_models_aggregator


class TestAllPassModelsAggregator:

    def test_empty_per_model_raises(self):
        agg = AllPassModelsAggregator()
        with pytest.raises(ValueError):
            agg.aggregate_models([], threshold=0.5, weights=[])

    def test_all_above_threshold_returns_min(self):
        agg = AllPassModelsAggregator()
        out = agg.aggregate_models(
            [ScoreResult(score=1.0), ScoreResult(score=0.8)],
            threshold=0.5,
            weights=[1.0, 1.0],
        )
        assert out.score == pytest.approx(0.8)

    def test_one_below_threshold_returns_min(self):
        agg = AllPassModelsAggregator()
        out = agg.aggregate_models(
            [ScoreResult(score=1.0), ScoreResult(score=0.0)],
            threshold=0.5,
            weights=[1.0, 1.0],
        )
        assert out.score == 0.0

    def test_single_model_returns_its_score(self):
        agg = AllPassModelsAggregator()
        out = agg.aggregate_models(
            [ScoreResult(score=0.7)],
            threshold=0.5,
            weights=[1.0],
        )
        assert out.score == pytest.approx(0.7)


class TestAnyPassModelsAggregator:

    def test_one_above_returns_max(self):
        agg = AnyPassModelsAggregator()
        out = agg.aggregate_models(
            [ScoreResult(score=1.0), ScoreResult(score=0.0)],
            threshold=0.5,
            weights=[1.0, 1.0],
        )
        assert out.score == pytest.approx(1.0)

    def test_all_below_returns_max_still_below(self):
        agg = AnyPassModelsAggregator()
        out = agg.aggregate_models(
            [ScoreResult(score=0.1), ScoreResult(score=0.2)],
            threshold=0.5,
            weights=[1.0, 1.0],
        )
        assert out.score == pytest.approx(0.2)


class TestMajorityPassModelsAggregator:

    def test_strict_majority_passes(self):
        agg = MajorityPassModelsAggregator()
        out = agg.aggregate_models(
            [ScoreResult(score=1.0), ScoreResult(score=1.0),
             ScoreResult(score=0.0)],
            threshold=0.5,
            weights=[1.0, 1.0, 1.0],
        )
        assert out.score == pytest.approx(2 / 3)

    def test_tie_returns_half(self):
        agg = MajorityPassModelsAggregator()
        out = agg.aggregate_models(
            [ScoreResult(score=1.0), ScoreResult(score=0.0)],
            threshold=0.5,
            weights=[1.0, 1.0],
        )
        assert out.score == pytest.approx(0.5)


class TestAverageModelsAggregator:

    def test_average_score(self):
        agg = AverageModelsAggregator()
        out = agg.aggregate_models(
            [ScoreResult(score=1.0), ScoreResult(score=0.0)],
            threshold=0.5,
            weights=[1.0, 1.0],
        )
        assert out.score == pytest.approx(0.5)


class TestWeightedAverageModelsAggregator:

    def test_weighted_mean(self):
        agg = WeightedAverageModelsAggregator()
        out = agg.aggregate_models(
            [ScoreResult(score=1.0), ScoreResult(score=0.0)],
            threshold=0.5,
            weights=[2.0, 1.0],
        )
        assert out.score == pytest.approx(2.0 / 3.0)

    def test_zero_weight_total_returns_zero(self):
        agg = WeightedAverageModelsAggregator()
        out = agg.aggregate_models(
            [ScoreResult(score=1.0), ScoreResult(score=1.0)],
            threshold=0.5,
            weights=[0.0, 0.0],
        )
        assert out.score == 0.0


class TestWeightedMajorityModelsAggregator:

    def test_weighted_majority_passes(self):
        agg = WeightedMajorityModelsAggregator()
        out = agg.aggregate_models(
            [ScoreResult(score=1.0), ScoreResult(score=0.0)],
            threshold=0.5,
            weights=[2.0, 1.0],
        )
        assert out.score == pytest.approx(2.0 / 3.0)

    def test_weighted_majority_tie_returns_half(self):
        agg = WeightedMajorityModelsAggregator()
        out = agg.aggregate_models(
            [ScoreResult(score=1.0), ScoreResult(score=0.0)],
            threshold=0.5,
            weights=[1.0, 1.0],
        )
        assert out.score == pytest.approx(0.5)


class TestSingleModelEquivalence:

    @pytest.mark.parametrize("agg_cls", [
        AllPassModelsAggregator,
        AnyPassModelsAggregator,
        AverageModelsAggregator,
        WeightedAverageModelsAggregator,
    ])
    def test_n1_continuous_score_preserved(self, agg_cls):
        agg = agg_cls()
        out = agg.aggregate_models(
            [ScoreResult(score=0.7)],
            threshold=0.5,
            weights=[1.0],
        )
        assert out.score == pytest.approx(0.7)

    @pytest.mark.parametrize("agg_cls", [
        MajorityPassModelsAggregator,
        WeightedMajorityModelsAggregator,
    ])
    def test_n1_majority_passes_and_fails(self, agg_cls):
        agg = agg_cls()
        out_pass = agg.aggregate_models(
            [ScoreResult(score=0.9)],
            threshold=0.5,
            weights=[1.0],
        )
        out_fail = agg.aggregate_models(
            [ScoreResult(score=0.1)],
            threshold=0.5,
            weights=[1.0],
        )
        assert out_pass.score == 1.0
        assert out_fail.score == 0.0


class TestGetBuiltinModelsAggregator:

    def test_known_names(self):
        for name in ("all_pass", "any_pass", "majority_pass", "avg", "weighted_avg", "weighted_majority"):
            assert get_builtin_models_aggregator(name) is not None

    def test_unknown_name_returns_none(self):
        assert get_builtin_models_aggregator("nope") is None
