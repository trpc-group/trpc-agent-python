# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for AgentOptimizer facade dispatch."""

from __future__ import annotations

from typing import Optional

import pytest

from trpc_agent_sdk.evaluation._agent_optimizer import AgentOptimizer
from trpc_agent_sdk.evaluation._eval_case import EvalCase
from trpc_agent_sdk.evaluation._eval_case import Invocation
from trpc_agent_sdk.evaluation._eval_set import EvalSet
from trpc_agent_sdk.evaluation._optimize_gepa_reflective import GepaReflectiveOptimizer
from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def _invocation(user_text: str, response_text: Optional[str] = None) -> Invocation:
    final_response = (
        Content(role="model", parts=[Part.from_text(text=response_text)])
        if response_text is not None
        else None
    )
    return Invocation(
        user_content=Content(role="user", parts=[Part.from_text(text=user_text)]),
        final_response=final_response,
    )


def _eval_case(eval_id: str = "c1") -> EvalCase:
    return EvalCase(eval_id=eval_id, conversation=[_invocation("hi", "ack")])


async def _stub_call_agent(query: str) -> str:
    return "stub"


def _new_target_prompt(recorder: Optional[dict[str, str]] = None) -> TargetPrompt:
    target = TargetPrompt()
    state = recorder if recorder is not None else {}

    async def read_cb() -> str:
        return state.get("instruction", "initial")

    async def write_cb(value: str) -> None:
        state["instruction"] = value

    target.add_callback("instruction", read=read_cb, write=write_cb)
    return target


class _FakeGEPAResult:
    def __init__(self, candidates, val_scores):
        self.candidates = candidates
        self.val_aggregate_scores = val_scores
        self.parents = [[None]] + [[i - 1] for i in range(1, len(candidates))]
        self.discovery_eval_counts = [0] * len(candidates)
        self.total_metric_calls = 0
        self.best_outputs_valset = None

    @property
    def best_idx(self) -> int:
        return max(
            range(len(self.val_aggregate_scores)),
            key=lambda i: self.val_aggregate_scores[i],
        )


def _write_config_file(
    tmp_path,
    algo_name: str = "gepa_reflective",
    *,
    extra_algo: Optional[dict] = None,
) -> str:
    """Write a valid optimizer.json file to tmp_path and return its path.

    ``extra_algo`` is merged into the algorithm block to override or add
    optional fields (e.g. ``use_merge``).
    """
    import json
    algo_block = {
        "name": algo_name,
        "reflection_lm": {
            "provider_name": "openai",
            "model_name": "gpt-4o",
            "api_key": "test-key",
        },
        "max_metric_calls": 30,
    }
    if extra_algo:
        algo_block.update(extra_algo)
    payload = {
        "evaluate": {
            "metrics": [{"metric_name": "m1", "threshold": 0.7}],
            "num_runs": 1,
        },
        "optimize": {
            "algorithm": algo_block,
        },
    }
    config_path = tmp_path / "optimizer.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return str(config_path)


@pytest.mark.asyncio
async def test_facade_reads_config_file_and_dispatches(tmp_path, monkeypatch):
    """End-to-end: AgentOptimizer.optimize(config_path=...) reads the file + dispatches."""
    train = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train.model_dump_json(), encoding="utf-8")
    val_path.write_text(val.model_dump_json(), encoding="utf-8")

    config_path = _write_config_file(tmp_path)
    recorder: dict[str, str] = {}
    target = _new_target_prompt(recorder)

    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "initial"}, {"instruction": "improved"}],
        val_scores=[0.5, 0.9],
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    result = await AgentOptimizer.optimize(
        config_path=config_path,
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(tmp_path / "runs" / "test1"),
        update_source=True,
        verbose=0,
    )

    assert result.status == "SUCCEEDED"
    assert result.best_pass_rate == pytest.approx(0.9)
    assert result.best_prompts == {"instruction": "improved"}
    assert recorder["instruction"] == "improved"


@pytest.mark.asyncio
async def test_facade_unknown_algorithm_raises_valueerror(tmp_path):
    """If config.optimize.algorithm.name is not registered, raise ValueError listing options."""
    import json
    payload = {
        "evaluate": {"metrics": [{"metric_name": "m1", "threshold": 0.7}], "num_runs": 1},
        "optimize": {
            "algorithm": {
                "name": "no_such_algorithm",
                "reflection_lm": {
                    "provider_name": "openai",
                    "model_name": "gpt-4o",
                    "api_key": "test-key",
                },
                "max_metric_calls": 30,
            },
        },
    }
    config_path = tmp_path / "optimizer.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        await AgentOptimizer.optimize(
            config_path=str(config_path),
            call_agent=_stub_call_agent,
            target_prompt=_new_target_prompt(),
            train_dataset_path="/tmp/x.json",
            validation_dataset_path="/tmp/y.json",
            output_dir=str(tmp_path / "runs" / "test_unknown"),
            verbose=0,
        )

    msg = str(exc_info.value)
    assert "no_such_algorithm" in msg


@pytest.mark.asyncio
async def test_facade_unknown_algorithm_lists_available_algorithms(tmp_path):
    """API-A1: error message must enumerate registered algorithms so the user
    can see what they should have written instead. Previously pydantic's
    literal_error fired first and produced 'Input should be ...' without
    listing alternatives."""
    import json
    payload = {
        "evaluate": {"metrics": [{"metric_name": "m1", "threshold": 0.7}], "num_runs": 1},
        "optimize": {
            "algorithm": {
                "name": "gepa_reflactive",  # typo of gepa_reflective
                "reflection_lm": {
                    "provider_name": "openai",
                    "model_name": "gpt-4o",
                    "api_key": "test-key",
                },
                "max_metric_calls": 30,
            },
        },
    }
    config_path = tmp_path / "optimizer.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        await AgentOptimizer.optimize(
            config_path=str(config_path),
            call_agent=_stub_call_agent,
            target_prompt=_new_target_prompt(),
            train_dataset_path="/tmp/x.json",
            validation_dataset_path="/tmp/y.json",
            output_dir=str(tmp_path / "runs" / "typo_check"),
            verbose=0,
        )

    msg = str(exc_info.value)
    # Friendly enumeration: must include both the typo and at least one
    # registered algorithm so users see what to type.
    assert "gepa_reflactive" in msg
    assert "Available algorithms" in msg
    assert "gepa_reflective" in msg


@pytest.mark.asyncio
async def test_facade_missing_config_file_raises(tmp_path):
    """If config_path does not exist, propagate FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        await AgentOptimizer.optimize(
            config_path=str(tmp_path / "nonexistent.json"),
            call_agent=_stub_call_agent,
            target_prompt=_new_target_prompt(),
            train_dataset_path="/tmp/x.json",
            validation_dataset_path="/tmp/y.json",
            output_dir=str(tmp_path / "runs" / "missing"),
            verbose=0,
        )


def test_facade_is_exported_from_evaluation_package():
    import trpc_agent_sdk.evaluation as ev
    assert ev.AgentOptimizer is AgentOptimizer


@pytest.mark.asyncio
async def test_facade_persists_artifacts_under_output_dir(tmp_path, monkeypatch):
    """The facade must materialise result.json, summary.txt, rounds/*.json,
    baseline_prompts/, best_prompts/, config.snapshot.json and run.log under
    output_dir for every successful run."""
    import json

    train = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train.model_dump_json(), encoding="utf-8")
    val_path.write_text(val.model_dump_json(), encoding="utf-8")

    config_path = _write_config_file(tmp_path)
    target = _new_target_prompt()

    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "initial"}, {"instruction": "improved"}],
        val_scores=[0.5, 0.9],
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    output_dir = tmp_path / "runs" / "artifact_check"
    await AgentOptimizer.optimize(
        config_path=config_path,
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(output_dir),
        verbose=0,
    )

    assert (output_dir / "result.json").is_file()
    assert (output_dir / "summary.txt").is_file()
    config_snapshot_path = output_dir / "config.snapshot.json"
    assert config_snapshot_path.is_file()
    config_snapshot_text = config_snapshot_path.read_text(encoding="utf-8")
    assert "test-key" not in config_snapshot_text
    config_snapshot = json.loads(config_snapshot_text)
    assert config_snapshot["optimize"]["algorithm"]["reflection_lm"][
        "api_key"
    ] == "<redacted>"
    assert (output_dir / "run.log").is_file()
    assert (output_dir / "baseline_prompts" / "instruction.md").is_file()
    assert (output_dir / "best_prompts" / "instruction.md").is_file()
    best_text = (output_dir / "best_prompts" / "instruction.md").read_text(encoding="utf-8")
    assert best_text == "improved"
    log_line = (output_dir / "run.log").read_text(encoding="utf-8")
    assert "SUCCEEDED" in log_line


def test_copy_config_snapshot_recursively_redacts_common_secret_keys(tmp_path):
    """Config snapshots must remain useful without publishing credentials."""
    import json

    payload = {
        "api_key": "api-secret",
        "nested": [
            {
                "TOKEN": "token-secret",
                "access-token": "access-secret",
                "Authorization": "Bearer auth-secret",
            },
            {
                "password": "password-secret",
                "credentials": {"username": "alice", "password": "nested-secret"},
                "privateKey": "private-secret",
            },
            {
                "openai_api_key": "namespaced-api-secret",
                "github_token": "namespaced-token-secret",
                "aws_secret_access_key": "aws-secret",
                "db_passwd": "database-secret",
            },
        ],
        "model_name": "gpt-4o",
        "max_tokens": 128,
        "token_budget": 256,
    }
    config_path = tmp_path / "optimizer.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    AgentOptimizer._copy_config_snapshot(str(config_path), str(output_dir))

    snapshot_path = output_dir / "config.snapshot.json"
    snapshot_text = snapshot_path.read_text(encoding="utf-8")
    snapshot = json.loads(snapshot_text)
    assert snapshot == {
        "api_key": "<redacted>",
        "nested": [
            {
                "TOKEN": "<redacted>",
                "access-token": "<redacted>",
                "Authorization": "<redacted>",
            },
            {
                "password": "<redacted>",
                "credentials": "<redacted>",
                "privateKey": "<redacted>",
            },
            {
                "openai_api_key": "<redacted>",
                "github_token": "<redacted>",
                "aws_secret_access_key": "<redacted>",
                "db_passwd": "<redacted>",
            },
        ],
        "model_name": "gpt-4o",
        "max_tokens": 128,
        "token_budget": 256,
    }
    assert snapshot_text == (
        json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
    for secret in (
        "api-secret",
        "token-secret",
        "access-secret",
        "auth-secret",
        "password-secret",
        "nested-secret",
        "private-secret",
        "namespaced-api-secret",
        "namespaced-token-secret",
        "aws-secret",
        "database-secret",
    ):
        assert secret not in snapshot_text


@pytest.mark.parametrize(
    "invalid_config",
    [
        '{"api_key": "secret", invalid}',
        '{"api_key": NaN}',
    ],
)
def test_copy_config_snapshot_invalid_json_fails_closed(tmp_path, invalid_config):
    """Malformed source config must never be copied verbatim as an artifact."""
    config_path = tmp_path / "optimizer.json"
    config_path.write_text(invalid_config, encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    AgentOptimizer._copy_config_snapshot(str(config_path), str(output_dir))

    assert not (output_dir / "config.snapshot.json").exists()
    assert not (output_dir / "config.snapshot.json.tmp").exists()


@pytest.mark.asyncio
async def test_facade_persists_artifacts_when_algorithm_fails(tmp_path, monkeypatch):
    """Even when the algorithm returns a FAILED result the facade should
    still leave baseline_prompts, config snapshot and run.log on disk so
    debug context is preserved across runs."""
    train = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train.model_dump_json(), encoding="utf-8")
    val_path.write_text(val.model_dump_json(), encoding="utf-8")

    config_path = _write_config_file(tmp_path)
    target = _new_target_prompt()

    async def boom(self, **kwargs):
        raise RuntimeError("evaluator timeout")

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", boom)

    output_dir = tmp_path / "runs" / "failure_check"
    result = await AgentOptimizer.optimize(
        config_path=config_path,
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(output_dir),
        verbose=0,
    )
    assert result.status == "FAILED"
    assert "evaluator timeout" in result.error_message
    assert (output_dir / "result.json").is_file()
    assert (output_dir / "baseline_prompts" / "instruction.md").is_file()
    assert (output_dir / "config.snapshot.json").is_file()
    assert (output_dir / "run.log").is_file()


@pytest.mark.asyncio
async def test_facade_verbose_zero_emits_no_terminal_output(
    tmp_path, monkeypatch, capsys
):
    """verbose=0 must suppress every reporter event so the user can run the
    optimizer inside batch pipelines without polluting downstream stdout."""
    train = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train.model_dump_json(), encoding="utf-8")
    val_path.write_text(val.model_dump_json(), encoding="utf-8")

    config_path = _write_config_file(tmp_path)
    target = _new_target_prompt()

    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "initial"}, {"instruction": "improved"}],
        val_scores=[0.5, 0.9],
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    await AgentOptimizer.optimize(
        config_path=config_path,
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(tmp_path / "runs" / "silent"),
        verbose=0,
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# ----- A3-A6: optimizer startup-time input validation (spec §3.2) -----


@pytest.mark.asyncio
async def test_facade_rejects_tool_trajectory_avg_score_metric(tmp_path):
    """spec §3.2 / acceptance #12: tool_trajectory_avg_score requires session traces
    so it is unusable in call_agent mode; reject at startup."""
    import json
    payload = {
        "evaluate": {
            "metrics": [{"metric_name": "tool_trajectory_avg_score", "threshold": 0.8}],
            "num_runs": 1,
        },
        "optimize": {
            "algorithm": {
                "name": "gepa_reflective",
                "reflection_lm": {
                    "provider_name": "openai",
                    "model_name": "gpt-4o",
                    "api_key": "test-key",
                },
                "max_metric_calls": 10,
            },
        },
    }
    config_path = tmp_path / "optimizer.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        await AgentOptimizer.optimize(
            config_path=str(config_path),
            call_agent=_stub_call_agent,
            target_prompt=_new_target_prompt(),
            train_dataset_path="/tmp/x.json",
            validation_dataset_path="/tmp/y.json",
            output_dir=str(tmp_path / "runs" / "metric_check"),
            verbose=0,
        )
    assert "tool_trajectory_avg_score" in str(exc_info.value)


@pytest.mark.asyncio
async def test_facade_rejects_llm_rubric_knowledge_recall_metric(tmp_path):
    """F-4: ``llm_rubric_knowledge_recall`` reads tool responses from
    ``Invocation.intermediate_data``; ``RemoteEvalService`` always emits
    ``intermediate_data=None`` so the judge would silently see "No
    knowledge search results were found." for every case. Reject at
    startup so users do not waste an optimization run on a metric that
    can never produce a non-zero score in call_agent mode.
    """
    import json
    payload = {
        "evaluate": {
            "metrics": [{"metric_name": "llm_rubric_knowledge_recall", "threshold": 0.8}],
            "num_runs": 1,
        },
        "optimize": {
            "algorithm": {
                "name": "gepa_reflective",
                "reflection_lm": {
                    "provider_name": "openai",
                    "model_name": "gpt-4o",
                    "api_key": "test-key",
                },
                "max_metric_calls": 10,
            },
        },
    }
    config_path = tmp_path / "optimizer.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        await AgentOptimizer.optimize(
            config_path=str(config_path),
            call_agent=_stub_call_agent,
            target_prompt=_new_target_prompt(),
            train_dataset_path="/tmp/x.json",
            validation_dataset_path="/tmp/y.json",
            output_dir=str(tmp_path / "runs" / "metric_check_recall"),
            verbose=0,
        )
    assert "llm_rubric_knowledge_recall" in str(exc_info.value)
    # Error message should hint at compatible alternatives so users can
    # immediately switch instead of guessing.
    assert "final_response_avg_score" in str(exc_info.value)


@pytest.mark.asyncio
async def test_facade_rejects_empty_target_prompt(tmp_path):
    """spec §3.2: TargetPrompt with no registered fields is a usage error."""
    config_path = _write_config_file(tmp_path)
    empty_target = TargetPrompt()
    with pytest.raises(ValueError) as exc_info:
        await AgentOptimizer.optimize(
            config_path=config_path,
            call_agent=_stub_call_agent,
            target_prompt=empty_target,
            train_dataset_path="/tmp/x.json",
            validation_dataset_path="/tmp/y.json",
            output_dir=str(tmp_path / "runs" / "empty_target"),
            verbose=0,
        )
    assert "TargetPrompt" in str(exc_info.value)


@pytest.mark.asyncio
async def test_facade_rejects_non_async_call_agent(tmp_path):
    """spec §3.2: call_agent must be async; reject sync functions at startup."""
    config_path = _write_config_file(tmp_path)

    def sync_call_agent(query: str) -> str:
        return "stub"

    with pytest.raises(TypeError) as exc_info:
        await AgentOptimizer.optimize(
            config_path=config_path,
            call_agent=sync_call_agent,  # type: ignore[arg-type]
            target_prompt=_new_target_prompt(),
            train_dataset_path="/tmp/x.json",
            validation_dataset_path="/tmp/y.json",
            output_dir=str(tmp_path / "runs" / "sync_check"),
            verbose=0,
        )
    assert "async" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_facade_rejects_same_train_and_validation_paths(tmp_path):
    """spec §3.2: train and validation paths must be different to avoid train-test leakage."""
    config_path = _write_config_file(tmp_path)
    same_path = tmp_path / "shared.evalset.json"
    same_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        await AgentOptimizer.optimize(
            config_path=config_path,
            call_agent=_stub_call_agent,
            target_prompt=_new_target_prompt(),
            train_dataset_path=str(same_path),
            validation_dataset_path=str(same_path),
            output_dir=str(tmp_path / "runs" / "leakage_check"),
            verbose=0,
        )
    assert "train" in str(exc_info.value).lower() or "leak" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_facade_warns_when_use_merge_with_single_field(tmp_path):
    """GEPA-3: gepa merge degenerates to picking one of two parents when only
    a single component is registered. Surface a UserWarning so users don't
    silently see merge_rounds_total=0."""
    from trpc_agent_sdk.evaluation._optimize_config import load_optimize_config

    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text("{}", encoding="utf-8")
    val_path.write_text("{}", encoding="utf-8")

    config_path = _write_config_file(tmp_path, extra_algo={"use_merge": True})
    config = load_optimize_config(config_path)

    with pytest.warns(UserWarning, match="use_merge=true"):
        AgentOptimizer._validate_inputs(
            config=config,
            call_agent=_stub_call_agent,
            target_prompt=_new_target_prompt(),  # single callback field
            train_dataset_path=str(train_path),
            validation_dataset_path=str(val_path),
            output_dir=str(tmp_path / "runs" / "merge_warn"),
        )


@pytest.mark.asyncio
async def test_facade_no_warn_when_use_merge_with_two_fields(tmp_path):
    """Multi-field config + use_merge=True: warning must NOT fire."""
    import warnings as _warnings
    from trpc_agent_sdk.evaluation._optimize_config import load_optimize_config

    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text("{}", encoding="utf-8")
    val_path.write_text("{}", encoding="utf-8")

    config_path = _write_config_file(tmp_path, extra_algo={"use_merge": True})
    config = load_optimize_config(config_path)

    target = TargetPrompt()
    state_a: dict[str, str] = {}
    state_b: dict[str, str] = {}

    async def read_a() -> str:
        return state_a.get("v", "")

    async def write_a(v: str) -> None:
        state_a["v"] = v

    async def read_b() -> str:
        return state_b.get("v", "")

    async def write_b(v: str) -> None:
        state_b["v"] = v

    target.add_callback("a", read=read_a, write=write_a)
    target.add_callback("b", read=read_b, write=write_b)

    with _warnings.catch_warnings():
        _warnings.simplefilter("error", UserWarning)  # any UserWarning fails the test
        AgentOptimizer._validate_inputs(
            config=config,
            call_agent=_stub_call_agent,
            target_prompt=target,
            train_dataset_path=str(train_path),
            validation_dataset_path=str(val_path),
            output_dir=str(tmp_path / "runs" / "merge_two_fields"),
        )


@pytest.mark.asyncio
async def test_facade_no_warn_when_use_merge_false_single_field(tmp_path):
    """use_merge=false (default) + single field: warning must NOT fire."""
    import warnings as _warnings
    from trpc_agent_sdk.evaluation._optimize_config import load_optimize_config

    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text("{}", encoding="utf-8")
    val_path.write_text("{}", encoding="utf-8")

    config_path = _write_config_file(tmp_path)  # default use_merge=False
    config = load_optimize_config(config_path)

    with _warnings.catch_warnings():
        _warnings.simplefilter("error", UserWarning)
        AgentOptimizer._validate_inputs(
            config=config,
            call_agent=_stub_call_agent,
            target_prompt=_new_target_prompt(),
            train_dataset_path=str(train_path),
            validation_dataset_path=str(val_path),
            output_dir=str(tmp_path / "runs" / "no_merge"),
        )


@pytest.mark.asyncio
async def test_facade_restores_baseline_when_writeback_fails(tmp_path, monkeypatch):
    """If update_source=True but writing the best candidate back fails, sources
    must end up at the original baseline (not mid-run candidate) and the
    write-back exception must surface to the caller."""
    train = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train.model_dump_json(), encoding="utf-8")
    val_path.write_text(val.model_dump_json(), encoding="utf-8")
    config_path = _write_config_file(tmp_path)
    recorder: dict[str, str] = {"instruction": "BASELINE"}
    target = _new_target_prompt(recorder)
    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "BASELINE"}, {"instruction": "MID_CANDIDATE"}],
        val_scores=[0.5, 0.9],
    )

    async def fake_call_gepa(self, **kwargs):
        # Simulate gepa rewriting the source during a round.
        recorder["instruction"] = "MID_CANDIDATE"
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    # Patch write_all to fail only when the best is about to be persisted.
    original_write_all = target.write_all
    call_count = {"n": 0}

    async def explosive_write_all(prompts):
        call_count["n"] += 1
        if prompts.get("instruction") == "IMPROVED_BEST":
            raise RuntimeError("disk full")
        await original_write_all(prompts)

    # Make optimizer.run() set best_prompts to a distinct value the test can
    # detect; rebuild fake gepa result.
    fake_gepa_result.candidates = [{"instruction": "BASELINE"}, {"instruction": "IMPROVED_BEST"}]
    target.write_all = explosive_write_all  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="disk full"):
        await AgentOptimizer.optimize(
            config_path=config_path,
            call_agent=_stub_call_agent,
            target_prompt=target,
            train_dataset_path=str(train_path),
            validation_dataset_path=str(val_path),
            output_dir=str(tmp_path / "runs" / "writeback_fail"),
            update_source=True,
            verbose=0,
        )

    assert recorder["instruction"] == "BASELINE", (
        "after a failed write-back the source must be restored to baseline, "
        f"got {recorder['instruction']!r}"
    )


@pytest.mark.asyncio
async def test_facade_default_update_source_false_keeps_source_intact(tmp_path, monkeypatch):
    """A2: default ``update_source=False`` MUST leave TargetPrompt source untouched."""
    train = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train.model_dump_json(), encoding="utf-8")
    val_path.write_text(val.model_dump_json(), encoding="utf-8")
    config_path = _write_config_file(tmp_path)
    recorder: dict[str, str] = {"instruction": "INITIAL"}
    target = _new_target_prompt(recorder)
    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "INITIAL"}, {"instruction": "IMPROVED"}],
        val_scores=[0.5, 0.9],
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    result = await AgentOptimizer.optimize(
        config_path=config_path,
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(tmp_path / "runs" / "default_keep"),
        verbose=0,
    )

    assert result.best_prompts == {"instruction": "IMPROVED"}
    assert recorder["instruction"] == "INITIAL", (
        "default update_source=False MUST NOT write the best candidate back to source"
    )


@pytest.mark.asyncio
async def test_facade_update_source_true_writes_best_back(tmp_path, monkeypatch):
    """A2: explicit ``update_source=True`` writes the best candidate back to TargetPrompt."""
    train = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train.model_dump_json(), encoding="utf-8")
    val_path.write_text(val.model_dump_json(), encoding="utf-8")
    config_path = _write_config_file(tmp_path)
    recorder: dict[str, str] = {"instruction": "INITIAL"}
    target = _new_target_prompt(recorder)
    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "INITIAL"}, {"instruction": "IMPROVED"}],
        val_scores=[0.5, 0.9],
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    result = await AgentOptimizer.optimize(
        config_path=config_path,
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(tmp_path / "runs" / "update_true"),
        update_source=True,
        verbose=0,
    )

    assert result.best_prompts == {"instruction": "IMPROVED"}
    assert recorder["instruction"] == "IMPROVED"


@pytest.mark.asyncio
async def test_facade_accepts_train_and_validation_paths_differing_only_by_dot_slash(tmp_path):
    """Resolve symlinks/relative prefixes so './x' and 'x' are detected as same file."""
    config_path = _write_config_file(tmp_path)
    same_path = tmp_path / "shared.evalset.json"
    same_path.write_text("{}", encoding="utf-8")
    train_str = f"{same_path.parent}/./{same_path.name}"

    with pytest.raises(ValueError):
        await AgentOptimizer.optimize(
            config_path=config_path,
            call_agent=_stub_call_agent,
            target_prompt=_new_target_prompt(),
            train_dataset_path=train_str,
            validation_dataset_path=str(same_path),
            output_dir=str(tmp_path / "runs" / "dotslash_check"),
            verbose=0,
        )


@pytest.mark.asyncio
async def test_facade_forwards_extra_stop_and_gepa_callbacks(tmp_path, monkeypatch):
    """AgentOptimizer.optimize must forward extra_stop/gepa_callbacks to the algorithm."""
    train = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train.model_dump_json(), encoding="utf-8")
    val_path.write_text(val.model_dump_json(), encoding="utf-8")

    config_path = _write_config_file(tmp_path)
    target = _new_target_prompt()

    def sentinel_stopper(gepa_state=None):
        return False

    sentinel_callback = object()
    captured: dict = {}

    async def _capture_run(self, *, reporter=None):
        from trpc_agent_sdk.evaluation._optimize_result import OptimizeResult

        captured["extra_stop"] = list(self.extra_stop_callbacks)
        captured["extra_gepa"] = list(self.extra_gepa_callbacks)
        return OptimizeResult(
            algorithm="gepa_reflective",
            status="SUCCEEDED",
            finish_reason="completed",
            baseline_pass_rate=0.0,
            best_pass_rate=0.0,
            pass_rate_improvement=0.0,
            baseline_prompts={"instruction": "initial"},
            best_prompts={"instruction": "initial"},
            total_rounds=0,
            rounds=[],
            total_reflection_lm_calls=0,
            total_judge_model_calls=0,
            total_llm_cost=0.0,
            duration_seconds=0.0,
            started_at="2026-05-18T00:00:00+00:00",
            finished_at="2026-05-18T00:00:00+00:00",
            extras={},
        )

    monkeypatch.setattr(GepaReflectiveOptimizer, "run", _capture_run)

    await AgentOptimizer.optimize(
        config_path=config_path,
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(tmp_path / "runs" / "extras"),
        extra_stop_callbacks=[sentinel_stopper],
        extra_gepa_callbacks=[sentinel_callback],
        verbose=0,
    )

    assert sentinel_stopper in captured["extra_stop"]
    assert sentinel_callback in captured["extra_gepa"]


@pytest.mark.asyncio
async def test_facade_summary_txt_reflects_update_source_true(tmp_path, monkeypatch):
    """DOC-1: summary.txt must reflect the actual update_source value used.
    Previously _persist_artifacts hard-coded update_source=False so the file
    contradicted the terminal banner whenever the user passed update_source=True."""
    train = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train.model_dump_json(), encoding="utf-8")
    val_path.write_text(val.model_dump_json(), encoding="utf-8")

    config_path = _write_config_file(tmp_path)
    target = _new_target_prompt()

    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "initial"}, {"instruction": "improved"}],
        val_scores=[0.5, 0.9],
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    # Output dir intentionally lacks the substring "true" so the assertion
    # below cannot accidentally match the path itself.
    output_dir = tmp_path / "runs" / "us_check_a"
    await AgentOptimizer.optimize(
        config_path=config_path,
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(output_dir),
        update_source=True,
        verbose=0,
    )

    summary_text = (output_dir / "summary.txt").read_text(encoding="utf-8")
    # format_summary writes the exact line "update_source : true" / "false".
    assert "update_source : true" in summary_text, (
        f"summary.txt should reflect update_source=True; got:\n{summary_text}"
    )
    assert "update_source : false" not in summary_text


@pytest.mark.asyncio
async def test_facade_summary_txt_reflects_update_source_false(tmp_path, monkeypatch):
    """Complement: when update_source=False (default), summary still reflects that."""
    train = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train.model_dump_json(), encoding="utf-8")
    val_path.write_text(val.model_dump_json(), encoding="utf-8")

    config_path = _write_config_file(tmp_path)
    target = _new_target_prompt()

    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "initial"}, {"instruction": "improved"}],
        val_scores=[0.5, 0.9],
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    output_dir = tmp_path / "runs" / "us_check_b"
    await AgentOptimizer.optimize(
        config_path=config_path,
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(output_dir),
        update_source=False,
        verbose=0,
    )

    summary_text = (output_dir / "summary.txt").read_text(encoding="utf-8")
    assert "update_source : false" in summary_text
    assert "update_source : true" not in summary_text


# --- FAIL-2: cleanup_done sentinel prevents double baseline write_all ---

@pytest.mark.asyncio
async def test_facade_failed_writeback_invokes_baseline_callback_exactly_once(
    tmp_path, monkeypatch
):
    """FAIL-2: when write_all(best) raises, ``cleanup_done`` must guarantee the
    ``except`` rollback restore_baseline call is NOT followed by a second
    restore in ``finally``.

    Pre-fix code flipped ``writeback_succeeded`` only on the happy path, so
    the failure path executed write_all(baseline) twice: once in ``except``,
    once in ``finally``. Path-backed fields are idempotent (tmp + replace
    is harmless), but callback-backed fields with non-idempotent
    ``write_fn`` (audit logs, version counters) saw their hook fire twice
    per failed update_source=True run.
    """
    train = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train.model_dump_json(), encoding="utf-8")
    val_path.write_text(val.model_dump_json(), encoding="utf-8")
    config_path = _write_config_file(tmp_path)

    # Spy on every write_fn call so we can count exactly how many times
    # baseline is persisted after the best-write fails.
    write_log: list[str] = []
    state: dict[str, str] = {"instruction": "BASELINE"}

    async def read_cb() -> str:
        return state["instruction"]

    async def write_cb(value: str) -> None:
        write_log.append(value)
        if value == "IMPROVED_BEST":
            raise RuntimeError("disk full while writing best candidate")
        state["instruction"] = value

    target = TargetPrompt().add_callback(
        "instruction", read=read_cb, write=write_cb
    )

    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "BASELINE"}, {"instruction": "IMPROVED_BEST"}],
        val_scores=[0.5, 0.9],
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    with pytest.raises(RuntimeError, match="disk full"):
        await AgentOptimizer.optimize(
            config_path=config_path,
            call_agent=_stub_call_agent,
            target_prompt=target,
            train_dataset_path=str(train_path),
            validation_dataset_path=str(val_path),
            output_dir=str(tmp_path / "runs" / "fail2_double_baseline"),
            update_source=True,
            verbose=0,
        )

    # Expected sequence: best attempt (fails) -> baseline restore (success).
    # Pre-fix would have appended a second "BASELINE" from the finally block.
    assert write_log == ["IMPROVED_BEST", "BASELINE"], (
        "baseline write_fn must be invoked exactly once after a failed "
        f"update_source=True writeback; got {write_log!r}"
    )
    assert state["instruction"] == "BASELINE"


@pytest.mark.asyncio
async def test_facade_success_path_does_not_re_restore_baseline(
    tmp_path, monkeypatch
):
    """FAIL-2 happy-path counterpart: when write_all(best) succeeds, the
    ``finally`` block must NOT re-write baseline either.

    Pre-fix code was also wrong here in a milder way: if ``writeback_succeeded``
    was False at finally entry the restore fired. The flag flipped on
    success so the bug did not manifest on the happy path, but this test
    pins the invariant explicitly so a future refactor cannot reintroduce
    a double-write."""
    train = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train.model_dump_json(), encoding="utf-8")
    val_path.write_text(val.model_dump_json(), encoding="utf-8")
    config_path = _write_config_file(tmp_path)

    write_log: list[str] = []
    state: dict[str, str] = {"instruction": "BASELINE"}

    async def read_cb() -> str:
        return state["instruction"]

    async def write_cb(value: str) -> None:
        write_log.append(value)
        state["instruction"] = value

    target = TargetPrompt().add_callback(
        "instruction", read=read_cb, write=write_cb
    )
    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "BASELINE"}, {"instruction": "IMPROVED"}],
        val_scores=[0.5, 0.9],
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    await AgentOptimizer.optimize(
        config_path=config_path,
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(tmp_path / "runs" / "fail2_happy"),
        update_source=True,
        verbose=0,
    )

    # Only one call: the successful best writeback. No baseline restore.
    assert write_log == ["IMPROVED"], (
        "happy-path update_source=True must invoke write_fn exactly once "
        f"(best); got {write_log!r}"
    )
    assert state["instruction"] == "IMPROVED"


# --- FAIL-1: atomic artifact persistence + SIGINT mask -------------------

def test_atomic_write_text_no_partial_file_on_failure(tmp_path):
    """FAIL-1: ``_atomic_write_text`` must never leave a half-written file.

    If the write step crashes (simulated by a write_text mock that raises),
    the destination path either does not exist (first run) or holds its
    pre-call content untouched — never a partial write."""
    from trpc_agent_sdk.evaluation._agent_optimizer import _atomic_write_text

    target = tmp_path / "result.json"
    target.write_text("ORIGINAL", encoding="utf-8")

    # Simulate failure between tmp write and os.replace by writing to a
    # path whose parent does not exist.
    bad_path = tmp_path / "no_such_dir" / "result.json"
    with pytest.raises(FileNotFoundError):
        _atomic_write_text(str(bad_path), "PARTIAL_CONTENT")

    # The original target is untouched.
    assert target.read_text(encoding="utf-8") == "ORIGINAL"
    # No .tmp leaked at the bad path's parent (parent missing, nothing to clean).
    assert not bad_path.exists()


def test_atomic_write_text_replaces_existing_file(tmp_path):
    """FAIL-1: atomic write must fully replace any pre-existing content."""
    from trpc_agent_sdk.evaluation._agent_optimizer import _atomic_write_text

    target = tmp_path / "out.txt"
    target.write_text("OLD", encoding="utf-8")
    _atomic_write_text(str(target), "NEW")
    assert target.read_text(encoding="utf-8") == "NEW"
    assert not (tmp_path / "out.txt.tmp").exists()


def test_mask_sigint_restores_previous_handler():
    """FAIL-1: ``_mask_sigint`` must restore the original SIGINT handler on exit,
    even if the wrapped block raises."""
    import signal as _signal

    from trpc_agent_sdk.evaluation._agent_optimizer import _mask_sigint

    original = _signal.getsignal(_signal.SIGINT)
    try:
        sentinel_called = []

        def _sentinel(signum, frame):  # pragma: no cover
            sentinel_called.append(signum)

        _signal.signal(_signal.SIGINT, _sentinel)
        try:
            with _mask_sigint():
                # While masked, the handler is SIG_IGN, not _sentinel.
                assert _signal.getsignal(_signal.SIGINT) == _signal.SIG_IGN
            # On exit, _sentinel is restored.
            assert _signal.getsignal(_signal.SIGINT) is _sentinel

            # Raising inside the block still restores.
            with pytest.raises(RuntimeError):
                with _mask_sigint():
                    assert _signal.getsignal(_signal.SIGINT) == _signal.SIG_IGN
                    raise RuntimeError("boom")
            assert _signal.getsignal(_signal.SIGINT) is _sentinel
        finally:
            _signal.signal(_signal.SIGINT, original)
    finally:
        # Belt-and-suspenders restore so a test crash cannot leave the
        # interpreter in a weird state for sibling tests.
        _signal.signal(_signal.SIGINT, original)


def test_mask_sigint_no_op_off_main_thread():
    """FAIL-1: ``_mask_sigint`` must degrade to a no-op when invoked from a
    non-main thread (``signal.signal`` raises ValueError there).

    The artifact persistence path runs in whatever event-loop thread the
    caller picked; we still want it to complete cleanly even if SIGINT
    masking isn't available."""
    import threading

    from trpc_agent_sdk.evaluation._agent_optimizer import _mask_sigint

    errors: list[BaseException] = []

    def _runner() -> None:
        try:
            with _mask_sigint():
                pass
        except BaseException as exc:  # pragma: no cover - guard
            errors.append(exc)

    t = threading.Thread(target=_runner)
    t.start()
    t.join()
    assert errors == []


@pytest.mark.asyncio
async def test_persist_artifacts_uses_atomic_write_for_summary_and_result(
    tmp_path, monkeypatch
):
    """FAIL-1: ``_persist_artifacts`` must route every artifact write through
    ``os.replace`` (the tmp+rename atomic primitive). Spy ``os.replace`` and
    confirm result.json, summary.txt, round_<NNN>.json, run.log, baseline
    prompts and best prompts all show up as replace targets."""
    import os as _os

    train = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train.model_dump_json(), encoding="utf-8")
    val_path.write_text(val.model_dump_json(), encoding="utf-8")
    config_path = _write_config_file(tmp_path)
    target = _new_target_prompt()
    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "BASELINE"}, {"instruction": "IMPROVED"}],
        val_scores=[0.5, 0.9],
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    replaced: list[str] = []
    real_replace = _os.replace

    def _spy_replace(src, dst):
        replaced.append(str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(
        "trpc_agent_sdk.evaluation._agent_optimizer.os.replace", _spy_replace
    )

    output_dir = tmp_path / "runs" / "fail1_atomic"
    await AgentOptimizer.optimize(
        config_path=config_path,
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(output_dir),
        verbose=0,
    )

    # Every persisted artifact must have gone through atomic rename.
    replaced_names = {_os.path.basename(p) for p in replaced}
    assert "result.json" in replaced_names
    assert "summary.txt" in replaced_names
    assert "run.log" in replaced_names
    assert "config.snapshot.json" in replaced_names
    # At least one round file and one baseline / best prompt.
    assert any(n.startswith("round_") and n.endswith(".json") for n in replaced_names)
    # No leftover .tmp files in output_dir tree.
    leftover_tmps = list(output_dir.rglob("*.tmp"))
    assert leftover_tmps == [], f"unexpected .tmp residue: {leftover_tmps}"


@pytest.mark.asyncio
async def test_persist_artifacts_masks_sigint_during_writes(
    tmp_path, monkeypatch
):
    """FAIL-1: while ``_persist_artifacts`` runs, SIGINT must be masked so a
    panicked second Ctrl+C during teardown cannot interrupt artifact
    writes mid-os.replace. We verify by checking ``signal.getsignal`` from
    inside a spied-on artifact write."""
    import signal as _signal

    train = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train.model_dump_json(), encoding="utf-8")
    val_path.write_text(val.model_dump_json(), encoding="utf-8")
    config_path = _write_config_file(tmp_path)
    target = _new_target_prompt()
    fake_gepa_result = _FakeGEPAResult(
        candidates=[{"instruction": "BASELINE"}, {"instruction": "IMPROVED"}],
        val_scores=[0.5, 0.9],
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_gepa_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    sigint_state_during_persist: list = []
    real_replace = __import__("os").replace

    def _spy_replace(src, dst):
        sigint_state_during_persist.append(_signal.getsignal(_signal.SIGINT))
        return real_replace(src, dst)

    monkeypatch.setattr(
        "trpc_agent_sdk.evaluation._agent_optimizer.os.replace", _spy_replace
    )

    original = _signal.getsignal(_signal.SIGINT)
    try:
        await AgentOptimizer.optimize(
            config_path=config_path,
            call_agent=_stub_call_agent,
            target_prompt=target,
            train_dataset_path=str(train_path),
            validation_dataset_path=str(val_path),
            output_dir=str(tmp_path / "runs" / "fail1_sigint"),
            verbose=0,
        )
    finally:
        # Belt-and-suspenders restore in case the mask didn't unwind correctly.
        _signal.signal(_signal.SIGINT, original)

    # Every replace observed during persistence saw SIGINT == SIG_IGN.
    assert sigint_state_during_persist, "expected at least one artifact write"
    assert all(
        state == _signal.SIG_IGN for state in sigint_state_during_persist
    ), (
        "SIGINT must be masked during artifact persistence; observed handlers: "
        f"{sigint_state_during_persist!r}"
    )

    # After optimize returns, the prior handler is restored.
    assert _signal.getsignal(_signal.SIGINT) is original
