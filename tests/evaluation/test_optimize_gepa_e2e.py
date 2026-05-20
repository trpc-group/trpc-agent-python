# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end registration test: config -> registry.get -> instantiate -> run."""

from __future__ import annotations

from typing import Optional

import pytest

from trpc_agent_sdk.evaluation._eval_case import EvalCase
from trpc_agent_sdk.evaluation._eval_case import Invocation
from trpc_agent_sdk.evaluation._eval_config import EvalConfig
from trpc_agent_sdk.evaluation._eval_set import EvalSet
from trpc_agent_sdk.evaluation._optimize_config import GepaReflectiveAlgo
from trpc_agent_sdk.evaluation._optimize_config import OptimizeConfig
from trpc_agent_sdk.evaluation._optimize_config import OptimizeConfigFile
from trpc_agent_sdk.evaluation._optimize_gepa_reflective import GepaReflectiveOptimizer
from trpc_agent_sdk.evaluation._optimize_model_options import OptimizeModelOptions
from trpc_agent_sdk.evaluation._optimize_registry import OPTIMIZER_REGISTRY
from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


# ---------------------------------------------------------------------------
# Fixtures shared by the e2e flow
# ---------------------------------------------------------------------------


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


def _make_config() -> OptimizeConfigFile:
    return OptimizeConfigFile(
        evaluate=EvalConfig(
            metrics=[{"metric_name": "m1", "threshold": 0.7}],
            num_runs=1,
        ),
        optimize=OptimizeConfig(
            algorithm=GepaReflectiveAlgo(
                name="gepa_reflective",
                reflection_lm=OptimizeModelOptions(
                    provider_name="openai",
                    model_name="gpt-4o",
                    api_key="test-key",
                ),
                max_metric_calls=30,
            ),
        ),
    )


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


# ---------------------------------------------------------------------------
# Registration contract: importing evaluation package registers algorithms
# ---------------------------------------------------------------------------


def test_evaluation_package_import_registers_gepa_reflective():
    """Importing the evaluation package triggers algorithm registration.

    Business code only needs ``import trpc_agent_sdk.evaluation`` to make
    ``OPTIMIZER_REGISTRY.get("gepa_reflective")`` work; algorithm modules do
    NOT register themselves as a side-effect of bare ``_optimize_gepa_*``
    imports.
    """
    import trpc_agent_sdk.evaluation  # noqa: F401  triggers registrations

    assert "gepa_reflective" in OPTIMIZER_REGISTRY.list_registered()
    assert OPTIMIZER_REGISTRY.get("gepa_reflective") is GepaReflectiveOptimizer


def test_registry_lookup_unknown_algorithm_lists_available():
    import trpc_agent_sdk.evaluation  # noqa: F401

    with pytest.raises(ValueError) as exc_info:
        OPTIMIZER_REGISTRY.get("not_a_real_algorithm")

    msg = str(exc_info.value)
    assert "not_a_real_algorithm" in msg
    assert "gepa_reflective" in msg


# ---------------------------------------------------------------------------
# End-to-end flow: config -> registry.get -> instantiate -> run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_config_to_run_succeeds(tmp_path, monkeypatch):
    """Simulate the business-side entry point:

        1. Parse OptimizeConfigFile (algorithm.name = "gepa_reflective").
        2. Look up class via OPTIMIZER_REGISTRY.get(name).
        3. Instantiate with the supplied call_agent / target_prompt / datasets.
        4. await optimizer.run() → OptimizeResult with status="SUCCEEDED".
    """
    import trpc_agent_sdk.evaluation  # noqa: F401

    train_evalset = EvalSet(eval_set_id="train", eval_cases=[_eval_case("c1")])
    val_evalset = EvalSet(eval_set_id="val", eval_cases=[_eval_case("c1")])
    train_path = tmp_path / "train.json"
    val_path = tmp_path / "val.json"
    train_path.write_text(train_evalset.model_dump_json(), encoding="utf-8")
    val_path.write_text(val_evalset.model_dump_json(), encoding="utf-8")

    config = _make_config()
    recorder: dict[str, str] = {}
    target = _new_target_prompt(recorder)

    algorithm_cls = OPTIMIZER_REGISTRY.get(config.optimize.algorithm.name)
    optimizer = algorithm_cls(
        config=config,
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
    )

    fake_result = _FakeGEPAResult(
        candidates=[{"instruction": "initial"}, {"instruction": "improved"}],
        val_scores=[0.5, 0.9],
    )

    async def fake_call_gepa(self, **kwargs):
        return fake_result

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)

    result = await optimizer.run()

    assert result.status == "SUCCEEDED"
    assert result.best_pass_rate == pytest.approx(0.9)
    assert result.best_prompts == {"instruction": "improved"}
    # BaseOptimizer.run() never writes back; write-back is owned by the
    # AgentOptimizer facade and gated by ``update_source``.
    assert result.best_prompts["instruction"] == "improved"


@pytest.mark.asyncio
async def test_e2e_registry_returns_instantiable_class():
    """Class returned by registry can be instantiated with the standard kwargs."""
    import trpc_agent_sdk.evaluation  # noqa: F401

    config = _make_config()
    target = _new_target_prompt()

    cls = OPTIMIZER_REGISTRY.get("gepa_reflective")
    instance = cls(
        config=config,
        call_agent=_stub_call_agent,
        target_prompt=target,
        train_dataset_path="/tmp/train.json",
        validation_dataset_path="/tmp/val.json",
    )

    assert isinstance(instance, GepaReflectiveOptimizer)
    assert instance.config is config
    assert instance.target_prompt is target
