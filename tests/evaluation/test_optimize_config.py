# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for OptimizeConfigFile and discriminated algorithm union."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from trpc_agent_sdk.evaluation._eval_config import EvalConfig
from trpc_agent_sdk.evaluation._optimize_config import GepaReflectiveAlgo
from trpc_agent_sdk.evaluation._optimize_config import OptimizeConfig
from trpc_agent_sdk.evaluation._optimize_config import OptimizeConfigFile
from trpc_agent_sdk.evaluation._optimize_config import load_optimize_config
from trpc_agent_sdk.evaluation._optimize_model_options import OptimizeModelOptions


_VALID_REFLECTION_LM = {
    "model_name": "gpt-4o",
    "api_key": "opt-key",
    "base_url": "https://api.example.com",
    "generation_config": {"temperature": 0.2},
}


def _evaluate_section_dict() -> dict:
    return {
        "metrics": [
            {
                "metric_name": "final_response_avg_score",
                "threshold": 0.7,
                "criterion": {"finalResponse": {}},
            }
        ],
        "num_runs": 2,
    }


def _gepa_algorithm_dict() -> dict:
    return {
        "name": "gepa_reflective",
        "reflection_lm": _VALID_REFLECTION_LM,
        "candidate_selection_strategy": "pareto",
        "module_selector": "round_robin",
        "use_merge": False,
        "max_merge_invocations": 5,
        "skip_perfect_score": True,
        "max_metric_calls": 50,
    }


def _full_config_dict_gepa() -> dict:
    return {
        "evaluate": _evaluate_section_dict(),
        "optimize": {
            "eval_case_parallelism": 8,
            "algorithm": {**_gepa_algorithm_dict(), "seed": 7},
        },
    }


def test_evaluate_section_is_plain_eval_config():
    payload = {
        "evaluate": {"metrics": [{"metric_name": "x", "threshold": 0.7}], "num_runs": 1},
        "optimize": {"algorithm": _gepa_algorithm_dict()},
    }
    cfg = OptimizeConfigFile.model_validate(payload)
    assert type(cfg.evaluate) is EvalConfig
    assert cfg.evaluate.num_runs == 1
    metrics = cfg.evaluate.get_eval_metrics()
    assert len(metrics) == 1
    assert metrics[0].metric_name == "x"


def test_evaluate_section_rejects_unknown_field_via_eval_config_forbid():
    payload = {
        "evaluate": {
            "metrics": [{"metric_name": "x", "threshold": 0.7}],
            "train_dataset_path": "unsupported",
        },
        "optimize": {"algorithm": _gepa_algorithm_dict()},
    }
    with pytest.raises(ValidationError):
        OptimizeConfigFile.model_validate(payload)


def test_gepa_reflective_algo_minimal_required_fields():
    algo = GepaReflectiveAlgo(
        name="gepa_reflective",
        reflection_lm=OptimizeModelOptions(model_name="gpt-4o", api_key="k"),
        max_metric_calls=10,
    )
    assert algo.name == "gepa_reflective"
    assert algo.reflection_lm.model_name == "gpt-4o"
    assert algo.seed == 42
    assert algo.candidate_selection_strategy == "pareto"
    assert algo.module_selector == "round_robin"
    assert algo.frontier_type == "instance"
    assert algo.use_merge is False
    assert algo.max_merge_invocations == 5
    assert algo.merge_val_overlap_floor == 5
    assert algo.skip_perfect_score is True
    assert algo.perfect_score == 1.0
    assert algo.cache_evaluation is False
    assert algo.track_best_outputs is False
    assert algo.reflection_minibatch_size is None
    assert algo.max_metric_calls == 10
    assert algo.max_iterations_without_improvement is None
    assert algo.timeout_seconds is None
    assert algo.score_threshold is None
    assert algo.max_candidate_proposals is None
    assert algo.max_tracked_candidates is None


def test_gepa_reflective_algo_rejects_unknown_field():
    with pytest.raises(ValidationError):
        GepaReflectiveAlgo(
            name="gepa_reflective",
            reflection_lm=OptimizeModelOptions(model_name="m", api_key="k"),
            max_metric_calls=10,
            typo_field=1,
        )


def test_gepa_reflective_algo_rejects_illegal_selection_strategy():
    with pytest.raises(ValidationError):
        GepaReflectiveAlgo(
            name="gepa_reflective",
            reflection_lm=OptimizeModelOptions(model_name="m", api_key="k"),
            max_metric_calls=10,
            candidate_selection_strategy="bogus",
        )


def test_gepa_reflective_algo_rejects_illegal_frontier_type():
    with pytest.raises(ValidationError):
        GepaReflectiveAlgo(
            name="gepa_reflective",
            reflection_lm=OptimizeModelOptions(model_name="m", api_key="k"),
            max_metric_calls=10,
            frontier_type="something_else",
        )


def test_gepa_reflective_algo_requires_at_least_one_stop_condition():
    with pytest.raises(ValidationError) as exc_info:
        GepaReflectiveAlgo(
            name="gepa_reflective",
            reflection_lm=OptimizeModelOptions(model_name="m", api_key="k"),
        )
    assert "stop condition" in str(exc_info.value).lower()


@pytest.mark.parametrize(
    "stop_field,stop_value",
    [
        ("max_iterations_without_improvement", 3),
        ("timeout_seconds", 10.0),
        ("score_threshold", 0.95),
        ("max_candidate_proposals", 25),
        ("max_tracked_candidates", 32),
    ],
)
def test_gepa_reflective_algo_accepts_any_single_stop_condition(stop_field, stop_value):
    algo = GepaReflectiveAlgo(
        name="gepa_reflective",
        reflection_lm=OptimizeModelOptions(model_name="m", api_key="k"),
        **{stop_field: stop_value},
    )
    assert getattr(algo, stop_field) == stop_value
    assert algo.max_metric_calls is None


def test_optimize_config_requires_algorithm():
    with pytest.raises(ValidationError):
        OptimizeConfig()


def test_optimize_config_routes_to_gepa_reflective():
    cfg = OptimizeConfig(algorithm=_gepa_algorithm_dict())
    assert isinstance(cfg.algorithm, GepaReflectiveAlgo)
    assert cfg.algorithm.name == "gepa_reflective"


def test_optimize_config_rejects_unknown_algorithm_name():
    with pytest.raises(ValidationError) as exc_info:
        OptimizeConfig(
            algorithm={
                "name": "unknown_algo",
                "reflection_lm": _VALID_REFLECTION_LM,
                "max_metric_calls": 10,
            }
        )
    assert "unknown_algo" in str(exc_info.value) or "tag" in str(exc_info.value).lower()


def test_optimize_config_rejects_missing_algorithm_name():
    with pytest.raises(ValidationError):
        OptimizeConfig(algorithm={"reflection_lm": _VALID_REFLECTION_LM})


def test_optimize_config_rejects_unknown_top_level_field():
    with pytest.raises(ValidationError):
        OptimizeConfig(algorithm=_gepa_algorithm_dict(), unknown_field="boom")


def test_optimize_config_seed_only_lives_under_algorithm():
    with pytest.raises(ValidationError):
        OptimizeConfig.model_validate(
            {"seed": 9, "algorithm": _gepa_algorithm_dict()}
        )

    cfg = OptimizeConfig.model_validate(
        {"algorithm": {**_gepa_algorithm_dict(), "seed": 9}}
    )
    assert isinstance(cfg.algorithm, GepaReflectiveAlgo)
    assert cfg.algorithm.seed == 9


def test_optimize_config_file_requires_both_sections():
    with pytest.raises(ValidationError):
        OptimizeConfigFile()
    with pytest.raises(ValidationError):
        OptimizeConfigFile(optimize=OptimizeConfig(algorithm=_gepa_algorithm_dict()))


def test_optimize_config_file_rejects_unknown_top_level_field():
    with pytest.raises(ValidationError):
        OptimizeConfigFile.model_validate(
            {
                "evaluate": _evaluate_section_dict(),
                "optimize": {"algorithm": _gepa_algorithm_dict()},
                "unknown_extra": 1,
            }
        )


def test_load_optimize_config_gepa_round_trip(tmp_path: Path):
    cfg_path = tmp_path / "opt.json"
    cfg_path.write_text(json.dumps(_full_config_dict_gepa()), encoding="utf-8")

    cfg = load_optimize_config(str(cfg_path))

    assert isinstance(cfg, OptimizeConfigFile)
    assert cfg.evaluate.num_runs == 2

    metrics = cfg.evaluate.get_eval_metrics()
    assert len(metrics) == 1
    assert metrics[0].metric_name == "final_response_avg_score"

    opt = cfg.optimize
    assert opt.eval_case_parallelism == 8

    assert isinstance(opt.algorithm, GepaReflectiveAlgo)
    assert opt.algorithm.reflection_lm.model_name == "gpt-4o"
    assert opt.algorithm.candidate_selection_strategy == "pareto"
    assert opt.algorithm.module_selector == "round_robin"
    assert opt.algorithm.seed == 7
    assert opt.algorithm.max_metric_calls == 50


def test_load_optimize_config_missing_evaluate_section_raises(tmp_path: Path):
    cfg_path = tmp_path / "no_evaluate.json"
    cfg_path.write_text(
        json.dumps({"optimize": {"algorithm": _gepa_algorithm_dict()}}),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_optimize_config(str(cfg_path))


def test_load_optimize_config_missing_optimize_section_raises(tmp_path: Path):
    cfg_path = tmp_path / "no_optimize.json"
    cfg_path.write_text(
        json.dumps({"evaluate": _evaluate_section_dict()}),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_optimize_config(str(cfg_path))


def test_load_optimize_config_missing_algorithm_raises(tmp_path: Path):
    cfg_path = tmp_path / "no_algo.json"
    cfg_path.write_text(
        json.dumps({"evaluate": _evaluate_section_dict(), "optimize": {}}),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_optimize_config(str(cfg_path))


def test_load_optimize_config_typo_in_evaluate_section_fails_fast(tmp_path: Path):
    cfg_path = tmp_path / "typo_eval.json"
    cfg_path.write_text(
        json.dumps(
            {
                "evaluate": {
                    "mertics": [{"metric_name": "x", "threshold": 0.7}],
                    "num_runs": 1,
                },
                "optimize": {"algorithm": _gepa_algorithm_dict()},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_optimize_config(str(cfg_path))


def test_load_optimize_config_typo_in_optimize_section_fails_fast(tmp_path: Path):
    cfg_path = tmp_path / "typo_opt.json"
    cfg_path.write_text(
        json.dumps(
            {
                "evaluate": _evaluate_section_dict(),
                "optimize": {
                    "maxRoundds": 5,
                    "algorithm": _gepa_algorithm_dict(),
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_optimize_config(str(cfg_path))


def test_load_optimize_config_typo_in_algorithm_fails_fast(tmp_path: Path):
    cfg_path = tmp_path / "typo_algo.json"
    bad_algo = _gepa_algorithm_dict()
    bad_algo["max_metricc_calls"] = 100
    cfg_path.write_text(
        json.dumps({"evaluate": _evaluate_section_dict(), "optimize": {"algorithm": bad_algo}}),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_optimize_config(str(cfg_path))


def test_load_optimize_config_unknown_algorithm_name_fails_fast(tmp_path: Path):
    cfg_path = tmp_path / "unknown_algo.json"
    cfg_path.write_text(
        json.dumps(
            {
                "evaluate": _evaluate_section_dict(),
                "optimize": {
                    "algorithm": {
                        "name": "few_shot_bayesian",
                        "reflection_lm": _VALID_REFLECTION_LM,
                        "max_metric_calls": 10,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_optimize_config(str(cfg_path))


def test_load_optimize_config_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_optimize_config(str(tmp_path / "does_not_exist.json"))


def test_load_optimize_config_camel_case_keys_accepted(tmp_path: Path):
    cfg_path = tmp_path / "camel.json"
    payload = {
        "evaluate": {
            "metrics": [{"metricName": "x", "threshold": 0.6}],
            "numRuns": 3,
        },
        "optimize": {
            "evalCaseParallelism": 5,
            "algorithm": {
                "name": "gepa_reflective",
                "reflectionLm": {"modelName": "claude-3.5-sonnet", "apiKey": "k"},
                "candidateSelectionStrategy": "current_best",
                "moduleSelector": "all",
                "useMerge": True,
                "maxMergeInvocations": 7,
                "skipPerfectScore": False,
                "maxMetricCalls": 30,
                "maxIterationsWithoutImprovement": 2,
            },
        },
    }
    cfg_path.write_text(json.dumps(payload), encoding="utf-8")
    cfg = load_optimize_config(str(cfg_path))
    assert cfg.evaluate.num_runs == 3
    assert cfg.optimize.eval_case_parallelism == 5
    assert isinstance(cfg.optimize.algorithm, GepaReflectiveAlgo)
    algo = cfg.optimize.algorithm
    assert algo.reflection_lm.model_name == "claude-3.5-sonnet"
    assert algo.candidate_selection_strategy == "current_best"
    assert algo.module_selector == "all"
    assert algo.use_merge is True
    assert algo.max_merge_invocations == 7
    assert algo.skip_perfect_score is False
    assert algo.max_metric_calls == 30
    assert algo.max_iterations_without_improvement == 2


def test_loaded_metrics_consumable_by_evaluator(tmp_path: Path):
    from trpc_agent_sdk.evaluation import EvalMetric

    cfg_path = tmp_path / "opt.json"
    cfg_path.write_text(json.dumps(_full_config_dict_gepa()), encoding="utf-8")
    cfg = load_optimize_config(str(cfg_path))
    metrics = cfg.evaluate.get_eval_metrics()
    for metric in metrics:
        assert isinstance(metric, EvalMetric)


# ---------------------------------------------------------------------------
# FrameworkStopConfig
# ---------------------------------------------------------------------------


def test_framework_stop_config_default_required_metrics_is_all():
    from trpc_agent_sdk.evaluation._optimize_config import FrameworkStopConfig

    cfg = FrameworkStopConfig()
    assert cfg.required_metrics == "all"


def test_framework_stop_config_accepts_metric_list():
    from trpc_agent_sdk.evaluation._optimize_config import FrameworkStopConfig

    cfg = FrameworkStopConfig(required_metrics=["m1", "m2"])
    assert cfg.required_metrics == ["m1", "m2"]


def test_framework_stop_config_accepts_none_to_disable():
    from trpc_agent_sdk.evaluation._optimize_config import FrameworkStopConfig

    cfg = FrameworkStopConfig(required_metrics=None)
    assert cfg.required_metrics is None


def test_framework_stop_config_accepts_empty_list_to_disable():
    from trpc_agent_sdk.evaluation._optimize_config import FrameworkStopConfig

    cfg = FrameworkStopConfig(required_metrics=[])
    assert cfg.required_metrics == []


def test_framework_stop_config_rejects_invalid_string():
    from trpc_agent_sdk.evaluation._optimize_config import FrameworkStopConfig

    with pytest.raises(ValidationError):
        FrameworkStopConfig(required_metrics="not-all")


def test_framework_stop_config_rejects_unknown_field():
    from trpc_agent_sdk.evaluation._optimize_config import FrameworkStopConfig

    with pytest.raises(ValidationError):
        FrameworkStopConfig(required_metrics="all", typo_field=True)


# ---------------------------------------------------------------------------
# OptimizeConfig.stop wiring
# ---------------------------------------------------------------------------


def test_optimize_config_stop_defaults_to_required_metrics_all():
    cfg = OptimizeConfig(algorithm=_gepa_algorithm_dict())
    assert cfg.stop.required_metrics == "all"


def test_optimize_config_stop_explicit_list():
    cfg = OptimizeConfig.model_validate(
        {"algorithm": _gepa_algorithm_dict(), "stop": {"required_metrics": ["m1"]}}
    )
    assert cfg.stop.required_metrics == ["m1"]


def test_optimize_config_top_level_fields():
    cfg = OptimizeConfig(algorithm=_gepa_algorithm_dict())
    assert cfg.eval_case_parallelism == 4
    assert set(OptimizeConfig.model_fields.keys()) == {
        "eval_case_parallelism",
        "stop",
        "algorithm",
    }


# ---------------------------------------------------------------------------
# OptimizeConfigFile cross-field validator
# ---------------------------------------------------------------------------


def test_optimize_config_file_cross_field_rejects_unknown_required_metric():
    with pytest.raises(ValidationError) as exc_info:
        OptimizeConfigFile.model_validate(
            {
                "evaluate": {
                    "metrics": [
                        {"metric_name": "m1", "threshold": 0.5},
                    ],
                },
                "optimize": {
                    "algorithm": _gepa_algorithm_dict(),
                    "stop": {"required_metrics": ["m1", "bogus"]},
                },
            }
        )
    assert "bogus" in str(exc_info.value)


def test_optimize_config_file_cross_field_accepts_known_required_metrics():
    cfg = OptimizeConfigFile.model_validate(
        {
            "evaluate": {
                "metrics": [
                    {"metric_name": "m1", "threshold": 0.5},
                    {"metric_name": "m2", "threshold": 0.3},
                ],
            },
            "optimize": {
                "algorithm": _gepa_algorithm_dict(),
                "stop": {"required_metrics": ["m1"]},
            },
        }
    )
    assert cfg.optimize.stop.required_metrics == ["m1"]


def test_optimize_config_file_cross_field_skipped_when_required_metrics_is_all():
    cfg = OptimizeConfigFile.model_validate(
        {
            "evaluate": {
                "metrics": [{"metric_name": "m1", "threshold": 0.5}],
            },
            "optimize": {
                "algorithm": _gepa_algorithm_dict(),
                "stop": {"required_metrics": "all"},
            },
        }
    )
    assert cfg.optimize.stop.required_metrics == "all"


def test_optimize_config_file_cross_field_skipped_when_required_metrics_is_none():
    cfg = OptimizeConfigFile.model_validate(
        {
            "evaluate": {
                "metrics": [{"metric_name": "m1", "threshold": 0.5}],
            },
            "optimize": {
                "algorithm": _gepa_algorithm_dict(),
                "stop": {"required_metrics": None},
            },
        }
    )
    assert cfg.optimize.stop.required_metrics is None


def test_optimize_config_file_no_stop_block_defaults_to_all():
    cfg = OptimizeConfigFile.model_validate(
        {
            "evaluate": {"metrics": [{"metric_name": "m1", "threshold": 0.5}]},
            "optimize": {"algorithm": _gepa_algorithm_dict()},
        }
    )
    assert cfg.optimize.stop.required_metrics == "all"


def test_load_optimize_config_with_stop_block_round_trip(tmp_path: Path):
    payload = _full_config_dict_gepa()
    payload["optimize"]["stop"] = {
        "required_metrics": ["final_response_avg_score"]
    }
    cfg_path = tmp_path / "with_stop.json"
    cfg_path.write_text(json.dumps(payload), encoding="utf-8")
    cfg = load_optimize_config(str(cfg_path))
    assert cfg.optimize.stop.required_metrics == ["final_response_avg_score"]


def test_gepa_reflective_algo_reflection_history_top_k_default_is_two() -> None:
    from trpc_agent_sdk.evaluation._optimize_config import GepaReflectiveAlgo
    from trpc_agent_sdk.evaluation._optimize_model_options import OptimizeModelOptions

    algo = GepaReflectiveAlgo(
        name="gepa_reflective",
        reflection_lm=OptimizeModelOptions(),
        max_metric_calls=1,
    )

    assert algo.reflection_history_top_k == 2


def test_gepa_reflective_algo_reflection_history_top_k_can_be_zero() -> None:
    """K=0 disables the feature."""
    from trpc_agent_sdk.evaluation._optimize_config import GepaReflectiveAlgo
    from trpc_agent_sdk.evaluation._optimize_model_options import OptimizeModelOptions

    algo = GepaReflectiveAlgo(
        name="gepa_reflective",
        reflection_lm=OptimizeModelOptions(),
        max_metric_calls=1,
        reflection_history_top_k=0,
    )

    assert algo.reflection_history_top_k == 0


def test_gepa_reflective_algo_reflection_history_top_k_rejects_six() -> None:
    """Cap at 5 to bound prompt-token blow-up."""
    import pytest
    from pydantic import ValidationError

    from trpc_agent_sdk.evaluation._optimize_config import GepaReflectiveAlgo
    from trpc_agent_sdk.evaluation._optimize_model_options import OptimizeModelOptions

    with pytest.raises(ValidationError):
        GepaReflectiveAlgo(
            name="gepa_reflective",
            reflection_lm=OptimizeModelOptions(),
            max_metric_calls=1,
            reflection_history_top_k=6,
        )
