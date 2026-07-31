"""Candidate-generation lifecycle tests for primary-error and artifact boundaries."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.pipeline.candidate_runtime import generate_candidate


class _DumpConfig:

    def model_dump(self, **kwargs):
        return {}


class _Workspace:
    baseline = {"system": "baseline"}

    def create_candidate_target(self, path: str):
        Path(path).mkdir(parents=True, exist_ok=False)
        return object()


class _ImportFailingSink:

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error or json.JSONDecodeError("half-written optimizer output", "{", 1)

    def phase_dir(self, name: str) -> None:
        return None

    def import_tree(self, source: str | Path, destination: str) -> None:
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize("primary", [asyncio.CancelledError("cancel"), RuntimeError("optimizer failed")])
async def test_artifact_import_failure_does_not_replace_generator_failure(primary) -> None:

    class FailingGenerator:

        async def generate(self, **kwargs):
            raise primary

    with pytest.raises(type(primary), match=str(primary)) as raised:
        await generate_candidate(
            generator=FailingGenerator(),
            workspace=_Workspace(),
            sink=_ImportFailingSink(),
            eval_config=_DumpConfig(),
            optimize_config=_DumpConfig(),
            train_attribution=object(),
            inner_train_path="inner-train.json",
            inner_selection_path="inner-selection.json",
        )
    assert any("artifact import also failed" in note for note in raised.value.__notes__)


@pytest.mark.asyncio
async def test_base_exception_during_import_does_not_replace_generator_failure() -> None:

    class FailingGenerator:

        async def generate(self, **kwargs):
            raise RuntimeError("optimizer failed first")

    sink = _ImportFailingSink(KeyboardInterrupt("import interrupted"))
    with pytest.raises(RuntimeError, match="optimizer failed first") as raised:
        await generate_candidate(
            generator=FailingGenerator(),
            workspace=_Workspace(),
            sink=sink,
            eval_config=_DumpConfig(),
            optimize_config=_DumpConfig(),
            train_attribution=object(),
            inner_train_path="inner-train.json",
            inner_selection_path="inner-selection.json",
        )
    assert any("KeyboardInterrupt: import interrupted" in note for note in raised.value.__notes__)
