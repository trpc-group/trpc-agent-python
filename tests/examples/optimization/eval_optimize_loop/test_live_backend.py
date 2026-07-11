from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from examples.optimization.eval_optimize_loop.pipeline.models import CandidateRecord, GateDecision, GateRuleResult
from examples.optimization.eval_optimize_loop.pipeline.optimizer_backend import (
    AgentOptimizerBackend,
    PipelineExecutionError,
    write_back_after_gate,
)
from examples.optimization.eval_optimize_loop.run_pipeline import main, run_live_pipeline
from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt


PROMPT_DIR = Path(__file__).resolve().parents[4] / "examples" / "optimization" / "eval_optimize_loop" / "agent" / "prompts"
EXAMPLE_DIR = PROMPT_DIR.parents[1]
DATASET = EXAMPLE_DIR / "train.evalset.json"


def _baseline() -> dict[str, str]:
    return {
        "system_prompt": (PROMPT_DIR / "system.md").read_text(encoding="utf-8"),
        "router_prompt": (PROMPT_DIR / "router.md").read_text(encoding="utf-8"),
    }


def _result(*, status: str = "SUCCEEDED", rounds: list[object] | None = None, best: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(status=status, best_prompts=best or _baseline(), rounds=rounds or [])


@pytest.mark.asyncio
async def test_live_backend_uses_sanitized_config_temporary_target_and_archives_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}

    async def fake_optimize(**kwargs):
        observed.update(kwargs)
        observed["target_read"] = await kwargs["target_prompt"].read_all()
        artifact = Path(kwargs["output_dir"]) / "sdk-artifact.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("created by SDK", encoding="utf-8")
        return _result()

    monkeypatch.setattr(AgentOptimizer, "optimize", fake_optimize)
    backend = AgentOptimizerBackend(
        raw_config={
            "evaluate": {"metrics": [], "num_runs": 1},
            "optimize": {"algorithm": {"name": "gepa_reflective", "reflection_lm": {"api_key": "${TRPC_AGENT_API_KEY}"}}},
            "pipeline": {"must_not": "reach SDK"},
        },
        candidate_scope="best_only",
    )

    candidates = await backend.generate_candidates(
        baseline_prompts=_baseline(), train_dataset_path=DATASET, validation_dataset_path=EXAMPLE_DIR / "val.evalset.json", output_dir=tmp_path
    )

    assert observed["update_source"] is False
    assert observed["verbose"] == 0
    assert Path(observed["output_dir"]).is_relative_to(tmp_path / "optimizer")
    assert observed["target_read"] == _baseline()
    runtime_config = Path(observed["config_path"])
    payload = json.loads(runtime_config.read_text(encoding="utf-8"))
    assert set(payload) == {"evaluate", "optimize"}
    assert payload["optimize"]["algorithm"]["reflection_lm"]["api_key"] == "${TRPC_AGENT_API_KEY}"
    assert (Path(observed["output_dir"]) / "sdk-artifact.txt").is_file()
    assert candidates[0].source == "agent_optimizer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "expected_ids", "expected_duplicates", "expected_skipped"),
    [
        ("best_only", ["best"], {}, []),
        ("accepted_rounds", ["round-001", "best"], {"round-001": ["round-003"]}, ["round-004"]),
        ("all", ["round-001", "round-002", "best"], {"round-001": ["round-003"]}, ["round-004"]),
    ],
)
async def test_candidate_extraction_respects_scope_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scope: str,
    expected_ids: list[str],
    expected_duplicates: dict[str, list[str]],
    expected_skipped: list[str],
) -> None:
    baseline = _baseline()
    candidate_one = {**baseline, "system_prompt": "GENERAL_FIX"}
    candidate_two = {**baseline, "system_prompt": "OVERFIT"}
    rounds = [
        SimpleNamespace(round=1, accepted=True, candidate_prompts=candidate_one, acceptance_reason="better"),
        SimpleNamespace(round=2, accepted=False, candidate_prompts=candidate_two, acceptance_reason="explore"),
        SimpleNamespace(round=3, accepted=True, candidate_prompts=candidate_one, acceptance_reason="duplicate"),
        SimpleNamespace(round=4, accepted=True, candidate_prompts={"system_prompt": "partial"}, acceptance_reason="partial"),
    ]

    async def fake_optimize(**_kwargs):
        return _result(rounds=rounds, best=baseline)

    monkeypatch.setattr(AgentOptimizer, "optimize", fake_optimize)
    backend = AgentOptimizerBackend(raw_config={"evaluate": {}, "optimize": {}}, candidate_scope=scope)
    records = await backend.generate_candidates(
        baseline_prompts=baseline, train_dataset_path=DATASET, validation_dataset_path=EXAMPLE_DIR / "val.evalset.json", output_dir=tmp_path
    )

    assert [record.candidate_id for record in records] == expected_ids
    assert backend.audit["duplicate_candidate_ids"] == expected_duplicates
    assert backend.audit["skipped_candidate_ids"] == expected_skipped


def test_live_cli_missing_environment_returns_two_without_constructing_optimizer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("optimizer must not be constructed")

    monkeypatch.setattr("examples.optimization.eval_optimize_loop.run_pipeline.run_live_pipeline", should_not_run)
    monkeypatch.setattr("sys.argv", ["run_pipeline.py", "--mode", "live", "--output-dir", str(tmp_path)])
    assert main() == 2
    assert "TRPC_AGENT_API_KEY" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_succeeded_optimizer_candidate_is_independently_evaluated_and_can_be_gate_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name, value in {
        "TRPC_AGENT_API_KEY": "unused-in-test",
        "TRPC_AGENT_BASE_URL": "https://unused.invalid",
        "TRPC_AGENT_MODEL_NAME": "unused",
    }.items():
        monkeypatch.setenv(name, value)
    baseline = _baseline()
    overfit = {**baseline, "system_prompt": "OVERFIT"}

    async def fake_optimize(**_kwargs):
        return _result(best=overfit)

    monkeypatch.setattr(AgentOptimizer, "optimize", fake_optimize)
    report = await run_live_pipeline(output_dir=tmp_path)
    assert report.mode == "live"
    assert report.candidates[0].candidate_id == "best"
    assert report.candidates[0].accepted is False
    assert report.candidates[0].train is not None
    assert report.candidates[0].validation is not None
    assert report.selected_candidate_id is None


@pytest.mark.asyncio
async def test_failed_optimizer_preserves_baseline_and_raises_pipeline_execution_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    baseline = _baseline()

    async def fake_optimize(**_kwargs):
        return _result(status="FAILED", best={**baseline, "system_prompt": "OVERFIT"})

    monkeypatch.setattr(AgentOptimizer, "optimize", fake_optimize)
    backend = AgentOptimizerBackend(raw_config={"evaluate": {}, "optimize": {}}, candidate_scope="best_only")
    with pytest.raises(PipelineExecutionError, match="FAILED"):
        await backend.generate_candidates(
            baseline_prompts=baseline, train_dataset_path=DATASET, validation_dataset_path=EXAMPLE_DIR / "val.evalset.json", output_dir=tmp_path
        )
    assert _baseline() == baseline


@pytest.mark.asyncio
async def test_optimizer_exception_is_wrapped_with_original_cause(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def fake_optimize(**_kwargs):
        raise RuntimeError("transport failed")

    monkeypatch.setattr(AgentOptimizer, "optimize", fake_optimize)
    backend = AgentOptimizerBackend(raw_config={"evaluate": {}, "optimize": {}}, candidate_scope="best_only")
    with pytest.raises(PipelineExecutionError, match="AgentOptimizer.optimize failed") as exc_info:
        await backend.generate_candidates(
            baseline_prompts=_baseline(),
            train_dataset_path=DATASET,
            validation_dataset_path=EXAMPLE_DIR / "val.evalset.json",
            output_dir=tmp_path,
        )
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_live_cli_returns_three_when_optimizer_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for name, value in {
        "TRPC_AGENT_API_KEY": "unused-in-test",
        "TRPC_AGENT_BASE_URL": "https://unused.invalid",
        "TRPC_AGENT_MODEL_NAME": "unused",
    }.items():
        monkeypatch.setenv(name, value)

    async def fake_optimize(**_kwargs):
        raise RuntimeError("transport failed")

    monkeypatch.setattr(AgentOptimizer, "optimize", fake_optimize)
    monkeypatch.setattr("sys.argv", ["run_pipeline.py", "--mode", "live", "--output-dir", str(tmp_path)])
    assert main() == 3
    assert "AgentOptimizer.optimize failed" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_optional_write_back_requires_gate_and_detects_baseline_conflict(tmp_path: Path) -> None:
    source = tmp_path / "system.md"
    source.write_text("BASELINE", encoding="utf-8")
    target = TargetPrompt().add_path("system_prompt", str(source))
    baseline = {"system_prompt": "BASELINE"}
    candidate = {"system_prompt": "CANDIDATE"}
    accepted = GateDecision(accepted=True, risk_level="low", rules=[], reasons=[])
    assert await write_back_after_gate(target, baseline, candidate, accepted) is True
    assert source.read_text(encoding="utf-8") == "CANDIDATE"
    source.write_text("CHANGED_EXTERNALLY", encoding="utf-8")
    with pytest.raises(PipelineExecutionError, match="baseline changed"):
        await write_back_after_gate(target, baseline, candidate, accepted)


@pytest.mark.asyncio
async def test_live_pipeline_does_not_write_back_by_default_or_without_gate_winner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name, value in {
        "TRPC_AGENT_API_KEY": "unused-in-test",
        "TRPC_AGENT_BASE_URL": "https://unused.invalid",
        "TRPC_AGENT_MODEL_NAME": "unused",
    }.items():
        monkeypatch.setenv(name, value)
    general_fix = {**_baseline(), "system_prompt": "GENERAL_FIX"}

    async def fake_optimize(**_kwargs):
        return _result(best=general_fix)

    async def should_not_write(*_args, **_kwargs):
        raise AssertionError("write-back must remain disabled by default")

    monkeypatch.setattr(AgentOptimizer, "optimize", fake_optimize)
    monkeypatch.setattr("examples.optimization.eval_optimize_loop.run_pipeline.write_back_after_gate", should_not_write)
    report = await run_live_pipeline(output_dir=tmp_path)
    assert report.selected_candidate_id == "best"


@pytest.mark.asyncio
async def test_live_pipeline_writes_only_selected_gate_winner_when_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from examples.optimization.eval_optimize_loop.pipeline.config import load_pipeline_config

    for name, value in {
        "TRPC_AGENT_API_KEY": "unused-in-test",
        "TRPC_AGENT_BASE_URL": "https://unused.invalid",
        "TRPC_AGENT_MODEL_NAME": "unused",
    }.items():
        monkeypatch.setenv(name, value)
    general_fix = {**_baseline(), "system_prompt": "GENERAL_FIX"}

    async def fake_optimize(**_kwargs):
        return _result(best=general_fix)

    config = load_pipeline_config(EXAMPLE_DIR / "optimizer.json", mode="live")
    enabled_pipeline = config.pipeline.model_copy(update={"write_back_when_accepted": True})
    enabled_config = config.model_copy(update={"pipeline": enabled_pipeline})
    observed: dict[str, object] = {}

    async def fake_write_back(target, baseline, candidate, gate):
        observed.update({"target": target, "baseline": baseline, "candidate": candidate, "gate": gate})
        return True

    monkeypatch.setattr(AgentOptimizer, "optimize", fake_optimize)
    monkeypatch.setattr("examples.optimization.eval_optimize_loop.run_pipeline.load_pipeline_config", lambda *_args, **_kwargs: enabled_config)
    monkeypatch.setattr("examples.optimization.eval_optimize_loop.run_pipeline.write_back_after_gate", fake_write_back)
    report = await run_live_pipeline(output_dir=tmp_path)
    assert report.selected_candidate_id == "best"
    assert observed["candidate"] == general_fix
    assert observed["gate"].accepted is True
