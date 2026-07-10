# Issue #91 Safe Closed-Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one auditable Evaluation + Optimization pipeline that satisfies Issue #91, rejects incomplete or regressing candidates, and writes source prompts only after the complete gate accepts them.

**Architecture:** Move orchestration from `run_pipeline.py` into a shared async pipeline used by both fake and SDK backends. Backends return the same complete schemas; the pipeline owns evaluation order, gate decisions, run-specific audit persistence, and post-gate transactional writeback.

**Tech Stack:** Python 3.11, dataclasses, asyncio, pathlib, `AgentEvaluator`, `AgentOptimizer`, `TargetPrompt`, pytest, YAPF, flake8.

---

## Working-tree rules

This worktree already contains intentional uncommitted changes in 17 Issue #91 files. Preserve them. Do not reset, restore, stash, or bulk-stage the worktree. Every task below stages only the listed files and commits one coherent change.

### Task 1: Establish the baseline and lock down data contracts

**Files:**
- Modify: `examples/optimization/eval_optimize_loop/eval_loop/schemas.py:12-157`
- Modify: `examples/optimization/eval_optimize_loop/eval_loop/loader.py:14-142`
- Modify: `examples/optimization/eval_optimize_loop/eval_loop/config.py:53-155`
- Modify: `examples/optimization/eval_optimize_loop/run_pipeline.py:771-814`
- Modify: `examples/optimization/eval_optimize_loop/tests/test_config_validation.py`
- Modify: `examples/optimization/eval_optimize_loop/tests/test_sdk_backend.py`

- [ ] **Step 1: Record the current baseline without changing files**

Run:

```powershell
git status --short
python -m pytest examples/optimization/eval_optimize_loop/tests --tb=short
```

Expected: the pre-existing Issue #91 files remain modified and the current suite reports `80 passed`.

- [ ] **Step 2: Add failing strict-input and path-collision tests**

Append these tests to `test_config_validation.py`:

```python
import math

import pytest

from examples.optimization.eval_optimize_loop.eval_loop.config import parse_optimizer_config
from examples.optimization.eval_optimize_loop.eval_loop.loader import read_json
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalCase
from examples.optimization.eval_optimize_loop.run_pipeline import _load_sdk_gate_config


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_read_json_rejects_non_standard_constants(tmp_path: Path, constant: str):
    path = tmp_path / "bad.json"
    path.write_text('{"value": ' + constant + "}", encoding="utf-8")

    with pytest.raises(ValueError, match="non-standard JSON constant"):
        read_json(path)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True])
def test_fake_gate_rejects_non_finite_or_boolean_numbers(value):
    with pytest.raises(ValueError, match="finite number"):
        parse_optimizer_config(
            {"gate": {"min_val_score_improvement": value}},
            path="optimizer.json",
        )


def test_sdk_gate_allows_explicitly_disabled_cost_limit(tmp_path: Path):
    path = tmp_path / "gate.json"
    path.write_text('{"gate": {"max_total_cost": null}}', encoding="utf-8")

    config = _load_sdk_gate_config(path)

    assert config["max_total_cost"] is None


def test_eval_case_rejects_explicit_split_mismatch():
    with pytest.raises(ValueError, match="split mismatch"):
        EvalCase.from_dict(
            {
                "id": "case-1",
                "split": "train",
                "input": "Return OK",
                "expectation": {"type": "exact", "expected": "OK"},
            },
            split="validation",
        )
```

Append this test to `test_sdk_backend.py`:

```python
def test_target_prompt_paths_reject_same_resolved_file(tmp_path: Path):
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")

    with pytest.raises(ValueError, match="same resolved file"):
        _parse_target_prompt_paths(
            [
                f"system_prompt={prompt_path}",
                f"router_prompt={prompt_path.parent / '.' / prompt_path.name}",
            ],
            default_prompt_path=prompt_path,
        )
```

- [ ] **Step 3: Run the new tests and confirm the current implementation fails**

Run:

```powershell
python -m pytest examples/optimization/eval_optimize_loop/tests/test_config_validation.py examples/optimization/eval_optimize_loop/tests/test_sdk_backend.py::test_target_prompt_paths_reject_same_resolved_file -v
```

Expected: failures show that Python JSON accepts non-standard constants, fake gate numbers accept `NaN`, split mismatch is not rejected, and aliased target paths are accepted.

- [ ] **Step 4: Extend the shared schemas without breaking existing callers**

In `schemas.py`, add defaults after the existing non-default `CaseResult` fields and add the new audit models:

```python
from typing import Any
from typing import Literal


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    split: str
    score: float
    passed: bool
    output: str
    metrics: dict[str, float] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    trace_available: bool = False
    failure_category: str | None = None
    failure_reason: str | None = None
    evidence: str | None = None
    cost: float = 0.0
    hard_failed: bool = False
    expected_failure_category: str | None = None


@dataclass(frozen=True)
class CandidatePrompt:
    candidate_id: str
    prompt: str
    rationale: str
    prompt_diff: str
    prompt_fields: dict[str, str] = field(default_factory=dict)

    def bundle(self) -> dict[str, str]:
        if self.prompt_fields:
            return dict(self.prompt_fields)
        return {"system_prompt": self.prompt}


@dataclass(frozen=True)
class CostSummary:
    optimizer: float = 0.0
    evaluator: float = 0.0
    agent: float = 0.0
    total: float = 0.0
    complete: bool = True


@dataclass(frozen=True)
class OptimizationRound:
    round_id: str
    candidate_id: str
    prompts: dict[str, str]
    rationale: str
    metrics: dict[str, float]
    cost: float
    duration_seconds: float


WritebackStatus = Literal[
    "rejected",
    "not_requested",
    "applied",
    "rolled_back",
    "rollback_failed",
]


@dataclass(frozen=True)
class WritebackResult:
    status: WritebackStatus
    before_hashes: dict[str, str] = field(default_factory=dict)
    after_hashes: dict[str, str] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class OptimizationResult:
    candidates: list[CandidatePrompt]
    rounds: list[OptimizationRound]
    cost: CostSummary
    raw_summary: dict[str, Any] = field(default_factory=dict)
```

Add `rounds`, `cost_summary`, and `writeback` to the end of `OptimizationReport`, with defaults so existing report construction remains valid during the refactor:

```python
    rounds: list[OptimizationRound] = field(default_factory=list)
    cost_summary: CostSummary = field(default_factory=CostSummary)
    writeback: WritebackResult = field(
        default_factory=lambda: WritebackResult(status="not_requested")
    )
```

- [ ] **Step 5: Implement strict JSON, finite-number, split, and path validation**

Replace `read_json()` in `loader.py` with:

```python
def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def read_json(path: str | Path) -> dict[str, Any]:
    resolved = Path(path)
    try:
        with resolved.open("r", encoding="utf-8") as file:
            payload = json.load(file, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid strict JSON in {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {resolved}")
    return payload
```

In `config.py`, use one helper for every numeric gate field:

```python
def _finite_number(value: Any, *, field_name: str, minimum: float, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite number")
    if parsed < minimum or (maximum is not None and parsed > maximum):
        upper = f" and <= {maximum}" if maximum is not None else ""
        raise ValueError(f"{field_name} must be >= {minimum}{upper}")
    return parsed
```

Use it for `min_val_score_improvement` and `max_score_drop_per_case`. Keep fake `GateConfig.max_total_cost` defaulted to `1.0`, but type it as `float | None`; accept `None` as “cost gate disabled” and otherwise validate it with `_finite_number()`. Apply the same rule in `_load_sdk_gate_config()`. In `EvalCase.from_dict()`, reject an explicit payload split that differs from the loader split. In `_parse_target_prompt_paths()`, track `Path(path).resolve()` values and raise `ValueError("--target-prompt fields must not reference the same resolved file")` on duplicates.

- [ ] **Step 6: Run focused and full example tests**

Run:

```powershell
python -m pytest examples/optimization/eval_optimize_loop/tests/test_config_validation.py examples/optimization/eval_optimize_loop/tests/test_sdk_backend.py::test_target_prompt_paths_reject_same_resolved_file -v
python -m pytest examples/optimization/eval_optimize_loop/tests --tb=short
```

Expected: all tests pass and no existing example test regresses.

- [ ] **Step 7: Commit the contract and validation change**

```powershell
git add examples/optimization/eval_optimize_loop/eval_loop/schemas.py examples/optimization/eval_optimize_loop/eval_loop/loader.py examples/optimization/eval_optimize_loop/eval_loop/config.py examples/optimization/eval_optimize_loop/run_pipeline.py examples/optimization/eval_optimize_loop/tests/test_config_validation.py examples/optimization/eval_optimize_loop/tests/test_sdk_backend.py
git commit -m "refactor(examples): define safe optimization contracts"
```

### Task 2: Add transactional prompt snapshots and post-gate writeback

**Files:**
- Create: `examples/optimization/eval_optimize_loop/eval_loop/writeback.py`
- Create: `examples/optimization/eval_optimize_loop/tests/test_writeback.py`

- [ ] **Step 1: Write failing snapshot, rollback, and compare-and-swap tests**

Create `test_writeback.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.eval_loop.writeback import ConcurrentPromptUpdateError
from examples.optimization.eval_optimize_loop.eval_loop.writeback import commit_prompt_bundle
from examples.optimization.eval_optimize_loop.eval_loop.writeback import snapshot_prompt_files
from examples.optimization.eval_optimize_loop.eval_loop.writeback import temporary_prompt_bundle


def _files(tmp_path: Path) -> dict[str, Path]:
    system = tmp_path / "system.txt"
    router = tmp_path / "router.txt"
    system.write_text("system baseline", encoding="utf-8")
    router.write_text("router baseline", encoding="utf-8")
    return {"system_prompt": system, "router_prompt": router}


def test_temporary_prompt_bundle_always_restores_original_bytes(tmp_path: Path):
    paths = _files(tmp_path)
    snapshot = snapshot_prompt_files(paths)

    with pytest.raises(RuntimeError, match="candidate failed"):
        with temporary_prompt_bundle(
            snapshot,
            {"system_prompt": "candidate system", "router_prompt": "candidate router"},
        ):
            assert paths["system_prompt"].read_text(encoding="utf-8") == "candidate system"
            raise RuntimeError("candidate failed")

    assert paths["system_prompt"].read_bytes() == b"system baseline"
    assert paths["router_prompt"].read_bytes() == b"router baseline"


def test_commit_rejects_concurrent_source_change(tmp_path: Path):
    paths = _files(tmp_path)
    snapshot = snapshot_prompt_files(paths)
    paths["system_prompt"].write_text("changed by another process", encoding="utf-8")

    with pytest.raises(ConcurrentPromptUpdateError):
        commit_prompt_bundle(
            snapshot,
            {"system_prompt": "candidate system", "router_prompt": "candidate router"},
        )

    assert paths["system_prompt"].read_text(encoding="utf-8") == "changed by another process"


def test_second_replace_failure_rolls_back_first_file(tmp_path: Path, monkeypatch):
    paths = _files(tmp_path)
    snapshot = snapshot_prompt_files(paths)
    real_replace = os.replace
    calls = 0

    def fail_second_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second replace failed")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second_replace)
    result = commit_prompt_bundle(
        snapshot,
        {"system_prompt": "candidate system", "router_prompt": "candidate router"},
    )

    assert result.status == "rolled_back"
    assert paths["system_prompt"].read_bytes() == b"system baseline"
    assert paths["router_prompt"].read_bytes() == b"router baseline"
```

- [ ] **Step 2: Run the tests and confirm the module is missing**

Run:

```powershell
python -m pytest examples/optimization/eval_optimize_loop/tests/test_writeback.py -v
```

Expected: collection fails because `eval_loop.writeback` does not exist.

- [ ] **Step 3: Implement the writeback module**

Create `writeback.py` with these public types and functions:

```python
from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .schemas import WritebackResult


class ConcurrentPromptUpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class PromptFileSnapshot:
    name: str
    path: Path
    content: bytes
    sha256: str


@dataclass(frozen=True)
class PromptSnapshot:
    files: dict[str, PromptFileSnapshot]

    def hashes(self) -> dict[str, str]:
        return {name: item.sha256 for name, item in self.files.items()}


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def snapshot_prompt_files(paths: dict[str, str | Path]) -> PromptSnapshot:
    files = {}
    for name, raw_path in paths.items():
        path = Path(raw_path)
        content = path.read_bytes()
        files[name] = PromptFileSnapshot(
            name=name,
            path=path,
            content=content,
            sha256=_hash_bytes(content),
        )
    return PromptSnapshot(files=files)


def _current_hashes(snapshot: PromptSnapshot) -> dict[str, str]:
    return {
        name: _hash_bytes(item.path.read_bytes())
        for name, item in snapshot.files.items()
    }


def _atomic_replace_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _restore_snapshot(snapshot: PromptSnapshot) -> list[str]:
    failures = []
    for name, item in snapshot.files.items():
        try:
            _atomic_replace_bytes(item.path, item.content)
        except OSError as exc:
            failures.append(f"{name}: {exc}")
    return failures


@contextmanager
def temporary_prompt_bundle(
    snapshot: PromptSnapshot,
    prompts: dict[str, str],
) -> Iterator[None]:
    missing = sorted(set(snapshot.files) - set(prompts))
    if missing:
        raise ValueError(f"candidate prompt bundle is missing fields: {missing}")
    try:
        for name, item in snapshot.files.items():
            _atomic_replace_bytes(item.path, prompts[name].encode("utf-8"))
        yield
    finally:
        failures = _restore_snapshot(snapshot)
        if failures:
            raise RuntimeError(f"failed to restore prompt snapshot: {failures}")
        if _current_hashes(snapshot) != snapshot.hashes():
            raise RuntimeError("restored prompt hashes do not match the original snapshot")


def commit_prompt_bundle(
    snapshot: PromptSnapshot,
    prompts: dict[str, str],
) -> WritebackResult:
    before_hashes = _current_hashes(snapshot)
    if before_hashes != snapshot.hashes():
        raise ConcurrentPromptUpdateError("source prompts changed after the run snapshot")
    try:
        for name, item in snapshot.files.items():
            _atomic_replace_bytes(item.path, prompts[name].encode("utf-8"))
    except (OSError, KeyError) as exc:
        rollback_failures = _restore_snapshot(snapshot)
        status = "rollback_failed" if rollback_failures else "rolled_back"
        return WritebackResult(
            status=status,
            before_hashes=before_hashes,
            after_hashes=_current_hashes(snapshot),
            error=f"{exc}; rollback_failures={rollback_failures}",
        )
    return WritebackResult(
        status="applied",
        before_hashes=before_hashes,
        after_hashes=_current_hashes(snapshot),
    )
```

- [ ] **Step 4: Run writeback tests**

Run:

```powershell
python -m pytest examples/optimization/eval_optimize_loop/tests/test_writeback.py -v
```

Expected: all three tests pass.

- [ ] **Step 5: Commit the transactional writeback component**

```powershell
git add examples/optimization/eval_optimize_loop/eval_loop/writeback.py examples/optimization/eval_optimize_loop/tests/test_writeback.py
git commit -m "feat(examples): add transactional prompt writeback"
```

### Task 3: Normalize fake and SDK backend contracts

**Files:**
- Modify: `examples/optimization/eval_optimize_loop/eval_loop/backends.py`
- Modify: `examples/optimization/eval_optimize_loop/tests/test_sdk_backend.py`
- Modify: `examples/optimization/eval_optimize_loop/eval_loop/evaluator.py`

- [ ] **Step 1: Replace the unsafe writeback expectation and add result-mapping tests**

Replace `test_sdk_backend_passes_update_source_true` with:

```python
def test_sdk_backend_never_delegates_source_writeback(tmp_path: Path, monkeypatch):
    calls = _install_fake_sdk(monkeypatch, best_prompt="optimized prompt")
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")

    SDKBackend(
        prompt_path=prompt_path,
        call_agent_path="fake_call_agent_module:call_agent",
    ).optimize(
        baseline_prompt="baseline",
        train_path=tmp_path / "train.evalset.json",
        val_path=tmp_path / "val.evalset.json",
        optimizer_config_path=tmp_path / "optimizer.json",
        output_dir=tmp_path / "out",
    )

    assert calls["update_source"] is False
    assert prompt_path.read_text(encoding="utf-8") == "baseline"
```

Add a mapping test using small fake SDK result objects:

```python
def test_sdk_result_mapping_preserves_metrics_trace_and_expected_label():
    metric = SimpleNamespace(
        metric_name="final_response_avg_score",
        score=0.5,
        eval_status="FAILED",
        details=SimpleNamespace(reason="response mismatch"),
    )
    invocation = SimpleNamespace(
        actual_invocation=SimpleNamespace(
            user_content={"parts": [{"text": "query"}]},
            final_response={"parts": [{"text": "actual"}]},
            intermediate_data={"tool_calls": [{"name": "lookup"}]},
        )
    )
    run = SimpleNamespace(
        final_eval_status="FAILED",
        overall_eval_metric_results=[metric],
        eval_metric_result_per_invocation=[invocation],
        error_message=None,
    )
    result = SimpleNamespace(
        results_by_eval_set_id={
            "set": SimpleNamespace(eval_results_by_eval_id={"case-1": [run]})
        }
    )
    expected = EvalCase(
        case_id="case-1",
        split="validation",
        input="query",
        expectation={"type": "exact", "expected": "expected"},
        expected_failure_category="final_response_mismatch",
    )

    converted = _eval_result_from_sdk_result(
        result,
        prompt_id="candidate",
        split="validation",
        expected_cases={"case-1": expected},
    )

    case = converted.cases[0]
    assert case.metrics == {"final_response_avg_score": 0.5}
    assert case.trace_available is True
    assert case.trace["final_response"] == "actual"
    assert case.expected_failure_category == "final_response_mismatch"
```

- [ ] **Step 2: Run the focused SDK tests and confirm failures**

Run:

```powershell
python -m pytest examples/optimization/eval_optimize_loop/tests/test_sdk_backend.py -k "never_delegates or preserves_metrics" -v
```

Expected: the old backend passes `update_source=True`, and the SDK converter lacks the new arguments and fields.

- [ ] **Step 3: Make backend operations async and return `OptimizationResult`**

In `backends.py`, define protocols and make both backends implement them:

```python
from typing import Protocol

from .schemas import OptimizationResult


class EvaluationBackend(Protocol):
    async def evaluate(
        self,
        *,
        prompt_id: str,
        prompts: dict[str, str],
        dataset_path: str | Path,
        split: str,
        trace: bool,
        artifact_dir: str | Path,
    ) -> EvalResult:
        raise NotImplementedError


class OptimizationBackend(Protocol):
    async def optimize_candidates(
        self,
        *,
        baseline_prompts: dict[str, str],
        baseline_train: EvalResult,
        failure_summary: dict[str, object],
        train_path: str | Path,
        validation_path: str | Path,
        config_path: str | Path,
        artifact_dir: str | Path,
    ) -> OptimizationResult:
        raise NotImplementedError
```

Keep the current synchronous `SDKBackend.optimize()` only as a compatibility wrapper around `optimize_async()`. Remove the `update_source` field from `SDKBackend`; in `optimize_async()` hard-code:

```python
        result = await AgentOptimizer.optimize(
            config_path=str(optimizer_config_path),
            call_agent=call_agent,
            target_prompt=target_prompt,
            train_dataset_path=str(train_path),
            validation_dataset_path=str(val_path),
            output_dir=str(output_dir),
            update_source=False,
            verbose=0,
        )
```

Extract all unique round candidates and the final best prompt into `CandidatePrompt.prompt_fields`; use IDs `sdk_round_001`, `sdk_round_002`, and `sdk_best`. Preserve `acceptance_reason`, metric breakdown, round cost, and duration in `OptimizationRound`.

- [ ] **Step 4: Preserve per-metric results and invocation trace**

Change `_eval_result_from_sdk_result()` to accept `expected_cases: dict[str, EvalCase]`. For each eval ID:

```python
def _metric_scores(runs: list[Any]) -> dict[str, float]:
    values: dict[str, list[float]] = {}
    for run in runs:
        for metric in getattr(run, "overall_eval_metric_results", []) or []:
            score = getattr(metric, "score", None)
            if isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(float(score)):
                values.setdefault(str(metric.metric_name), []).append(float(score))
    return {name: round(sum(items) / len(items), 6) for name, items in values.items()}


def _invocation_trace(runs: list[Any]) -> tuple[dict[str, Any], bool]:
    if not runs:
        return {}, False
    invocation_results = getattr(runs[-1], "eval_metric_result_per_invocation", []) or []
    if not invocation_results:
        return {}, False
    actual = getattr(invocation_results[-1], "actual_invocation", None)
    if actual is None:
        return {}, False
    return {
        "user_content": _safe_jsonable(getattr(actual, "user_content", None)),
        "final_response": _content_text(getattr(actual, "final_response", None)),
        "intermediate_data": _safe_jsonable(getattr(actual, "intermediate_data", None)),
    }, True
```

Set `CaseResult.score` to the mean of the metric map, preserve `expected_failure_category`, and validate that the SDK result case IDs exactly match `expected_cases`.

- [ ] **Step 5: Run backend tests and the example suite**

```powershell
python -m pytest examples/optimization/eval_optimize_loop/tests/test_sdk_backend.py -v
python -m pytest examples/optimization/eval_optimize_loop/tests --tb=short
```

Expected: all tests pass; tests that previously asserted delegated source writes now assert `False`.

- [ ] **Step 6: Commit the backend normalization**

```powershell
git add examples/optimization/eval_optimize_loop/eval_loop/backends.py examples/optimization/eval_optimize_loop/eval_loop/evaluator.py examples/optimization/eval_optimize_loop/tests/test_sdk_backend.py
git commit -m "refactor(examples): normalize optimization backends"
```

### Task 4: Remove fake-model oracle and split leakage

**Files:**
- Modify: `examples/optimization/eval_optimize_loop/eval_loop/fake_model.py`
- Modify: `examples/optimization/eval_optimize_loop/eval_loop/optimizer.py`
- Modify: `examples/optimization/eval_optimize_loop/eval_loop/backends.py`
- Modify: `examples/optimization/eval_optimize_loop/data/train.evalset.json`
- Modify: `examples/optimization/eval_optimize_loop/data/val.evalset.json`
- Modify: `examples/optimization/eval_optimize_loop/tests/test_fake_model_generalization.py`
- Modify: `examples/optimization/eval_optimize_loop/tests/test_no_sample_case_id_hardcoding.py`

- [ ] **Step 1: Replace oracle-dependent tests with metamorphic tests**

Replace `test_fake_model_generalization.py` with tests that hold user input and prompt constant while changing evaluator-only fields:

```python
from __future__ import annotations

from examples.optimization.eval_optimize_loop.eval_loop.fake_model import FakeModel
from examples.optimization.eval_optimize_loop.eval_loop.optimizer import FakeOptimizer
from examples.optimization.eval_optimize_loop.eval_loop.schemas import CaseResult
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalCase
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalResult


def _case(*, split: str, expected: str, protected: bool) -> EvalCase:
    return EvalCase(
        case_id=f"case-{split}-{expected}",
        split=split,
        input="Return only PUBLIC; do not use JSON.",
        expectation={"type": "exact", "expected": expected},
        protected=protected,
        tags=["evaluator-only"],
    )


def test_fake_model_output_does_not_change_with_oracle_fields():
    model = FakeModel(seed=91)
    first, _, _ = model.generate("candidate", "baseline prompt", _case(split="train", expected="SECRET", protected=False))
    second, _, _ = model.generate("candidate", "baseline prompt", _case(split="validation", expected="OTHER", protected=True))

    assert first == "PUBLIC"
    assert second == "PUBLIC"


def test_fake_optimizer_requires_observed_training_failures():
    passing = EvalResult(
        prompt_id="baseline",
        split="train",
        score=1.0,
        passed=True,
        cost=0.0,
        cases=[CaseResult(case_id="c1", split="train", score=1.0, passed=True, output="OK")],
    )
    failing = EvalResult(
        prompt_id="baseline",
        split="train",
        score=0.0,
        passed=False,
        cost=0.0,
        cases=[
            CaseResult(
                case_id="c1",
                split="train",
                score=0.0,
                passed=False,
                output="bad",
                failure_category="format_violation",
                failure_reason="not strict JSON",
            )
        ],
    )

    optimizer = FakeOptimizer()
    assert optimizer.propose("baseline", passing, {"by_category": {}}) == []
    candidates = optimizer.propose(
        "baseline",
        failing,
        {"by_category": {"format_violation": 1}},
    )
    assert [candidate.candidate_id for candidate in candidates] == [
        "candidate_001_overfit",
        "candidate_002_safe",
    ]
```

- [ ] **Step 2: Run the metamorphic tests and confirm leakage**

```powershell
python -m pytest examples/optimization/eval_optimize_loop/tests/test_fake_model_generalization.py -v
```

Expected: outputs differ when split, protected, or expected value changes, and `FakeOptimizer.propose()` has the old signature.

- [ ] **Step 3: Make fake behavior depend only on input and prompt**

Replace expectation-driven output helpers in `fake_model.py` with input parsers:

```python
import json
import re


ASSIGNMENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=([A-Za-z0-9_-]+)\b")
ONLY_RE = re.compile(r"return only\s+([A-Za-z0-9_-]+)", re.IGNORECASE)


def _assignments(text: str) -> dict[str, str]:
    return {key: value for key, value in ASSIGNMENT_RE.findall(text)}


def _only_value(text: str) -> str | None:
    match = ONLY_RE.search(text)
    return match.group(1) if match else None


class FakeModel:
    COST_PER_CALL = 0.001

    def __init__(self, seed: int = 91) -> None:
        self.seed = seed

    def generate(self, prompt_id: str, prompt: str, case: EvalCase) -> tuple[str, dict[str, Any], float]:
        mode = self._mode(prompt)
        output = self._render(mode, case.input)
        return output, {
            "seed": self.seed,
            "prompt_id": prompt_id,
            "prompt_mode": mode,
            "case_id": case.case_id,
        }, self.COST_PER_CALL

    def _mode(self, prompt: str) -> str:
        if "Always force every final answer into JSON" in prompt:
            return "overfit"
        if "Use strict JSON only when the user explicitly asks" in prompt:
            return "safe"
        return "baseline"

    def _render(self, mode: str, user_input: str) -> str:
        assignments = _assignments(user_input)
        only_value = _only_value(user_input)
        asks_json = "strict json" in user_input.lower()
        natural = self._natural_answer(user_input)
        if mode == "overfit":
            payload = assignments or {"answer": only_value or natural}
            return json.dumps(payload, sort_keys=True)
        if asks_json:
            payload = json.dumps(assignments, sort_keys=True)
            return payload if mode == "safe" else f"Here is the JSON you requested: {payload}"
        if only_value is not None:
            return only_value
        return natural

    def _natural_answer(self, user_input: str) -> str:
        lowered = user_input.lower()
        if "latency" in lowered and "retries" in lowered:
            return "Latency and retries need monitoring."
        if "cache" in lowered and "stale data" in lowered:
            return "Cache invalidation prevents stale data."
        return "The request was handled naturally."
```

Delete all reads of `case.expectation`, `case.split`, `case.protected`, `case.tags`, and `case.simulated_outputs` from `FakeModel`.

- [ ] **Step 4: Make fake optimization consume baseline failures**

Change `FakeOptimizer.propose()` to:

```python
def propose(
    self,
    baseline_prompt: str,
    baseline_train: EvalResult,
    failure_summary: dict[str, object],
) -> list[CandidatePrompt]:
    failed_categories = {
        case.failure_category
        for case in baseline_train.cases
        if not case.passed and case.failure_category
    }
    summarized = set((failure_summary.get("by_category") or {}).keys())
    if not (failed_categories | summarized):
        return []
    if not ((failed_categories | summarized) & {"format_violation", "final_response_mismatch"}):
        return []
    return self._format_candidates(baseline_prompt)
```

Move the two existing candidate constructors into `_format_candidates()`, remove `OPTIMIZER_MARKER`, and keep the natural-language instructions used by `FakeModel._mode()`.

- [ ] **Step 5: Update the six public user inputs without exposing judge labels to the model**

Keep official SDK EvalSet shape. Use these user texts and evaluator expectations:

```json
{
  "train": [
    ["Return strict JSON with intent=refund and priority=high.", {"type": "json", "expected_values": {"intent": "refund", "priority": "high"}}],
    ["Return strict JSON with status=READY and next_step=ship.", {"type": "json", "expected_values": {"status": "READY", "next_step": "ship"}}],
    ["Explain latency and retries naturally in under 80 characters.", {"type": "rubric", "must_include": ["latency", "retries"], "max_chars": 80}]
  ],
  "validation": [
    ["Return strict JSON with status=approved and next_step=email_customer.", {"type": "json", "expected_values": {"status": "approved", "next_step": "email_customer"}}],
    ["Explain cache invalidation and stale data naturally.", {"type": "rubric", "must_include": ["cache", "stale data"], "forbidden": ["{", "}", "json"]}],
    ["Return only YES; do not use JSON.", {"type": "exact", "expected": "YES"}]
  ]
}
```

Mark the final validation case protected. This yields baseline train `1/3`, overfit train `2/3`, baseline validation `2/3`, overfit validation `1/3`, and safe validation `3/3` without reading split.

- [ ] **Step 6: Run fake model and pipeline tests**

```powershell
python -m pytest examples/optimization/eval_optimize_loop/tests/test_fake_model_generalization.py examples/optimization/eval_optimize_loop/tests/test_no_sample_case_id_hardcoding.py examples/optimization/eval_optimize_loop/tests/test_pipeline_fake_mode.py -v
```

Expected: same input/prompt produces the same output across oracle-field changes; the overfit candidate is rejected and the safe candidate is selected.

- [ ] **Step 7: Commit the oracle-free fake backend**

```powershell
git add examples/optimization/eval_optimize_loop/eval_loop/fake_model.py examples/optimization/eval_optimize_loop/eval_loop/optimizer.py examples/optimization/eval_optimize_loop/eval_loop/backends.py examples/optimization/eval_optimize_loop/data/train.evalset.json examples/optimization/eval_optimize_loop/data/val.evalset.json examples/optimization/eval_optimize_loop/tests/test_fake_model_generalization.py examples/optimization/eval_optimize_loop/tests/test_no_sample_case_id_hardcoding.py
git commit -m "fix(examples): remove fake evaluation oracle leakage"
```

### Task 5: Build the shared async pipeline and complete gate

**Files:**
- Create: `examples/optimization/eval_optimize_loop/eval_loop/pipeline.py`
- Create: `examples/optimization/eval_optimize_loop/tests/test_pipeline_orchestration.py`
- Modify: `examples/optimization/eval_optimize_loop/eval_loop/gate.py`
- Modify: `examples/optimization/eval_optimize_loop/eval_loop/report.py`
- Modify: `examples/optimization/eval_optimize_loop/run_pipeline.py`

- [ ] **Step 1: Write orchestration tests with recording backends**

Create `test_pipeline_orchestration.py` with the following complete recording backend. It uses temporary official-shaped input files and records every backend call without patching production code:

```python
def _case(case_id: str, split: str, score: float) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        split=split,
        score=score,
        passed=score >= 1.0,
        output="OK" if score >= 1.0 else "FAIL",
        metrics={"exact_match": score},
        trace={"final_response": "OK" if score >= 1.0 else "FAIL"},
        trace_available=True,
        hard_failed=score == 0.0,
        cost=0.01,
    )


def _result(prompt_id: str, split: str, scores: list[float]) -> EvalResult:
    cases = [_case(f"{split[0]}{index}", split, score) for index, score in enumerate(scores)]
    return EvalResult(
        prompt_id=prompt_id,
        split=split,
        score=sum(scores) / len(scores),
        passed=all(case.passed for case in cases),
        cost=sum(case.cost for case in cases),
        cases=cases,
    )


class RecordingBackend:
    def __init__(
        self,
        tmp_path: Path,
        *,
        candidate_regresses: bool = False,
        cost_complete: bool = True,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.candidate_regresses = candidate_regresses
        self.cost_complete = cost_complete
        self.train_path = DEFAULT_TRAIN
        self.val_path = DEFAULT_VAL
        self.config_path = DEFAULT_OPTIMIZER_CONFIG
        self.prompt_path = tmp_path / "prompt.txt"
        self.prompt_path.write_text("baseline prompt", encoding="utf-8")

    async def evaluate(self, *, prompt_id: str, split: str, **kwargs: Any) -> EvalResult:
        self.calls.append(("evaluate", prompt_id, split))
        if prompt_id == "baseline":
            scores = [0.0, 1.0, 1.0]
        elif self.candidate_regresses and split == "validation":
            scores = [0.0, 0.0, 1.0]
        else:
            scores = [1.0, 1.0, 1.0]
        return _result(prompt_id, split, scores)

    async def optimize_candidates(self, **kwargs: Any) -> OptimizationResult:
        self.calls.append(("optimize",))
        prompt = "candidate safe prompt"
        candidate = CandidatePrompt(
            candidate_id="candidate_safe",
            prompt=prompt,
            rationale="fix observed baseline failure",
            prompt_diff="-baseline prompt\n+candidate safe prompt",
            prompt_fields={"system_prompt": prompt},
        )
        return OptimizationResult(
            candidates=[candidate],
            rounds=[
                OptimizationRound(
                    round_id="round_001",
                    candidate_id=candidate.candidate_id,
                    prompts=candidate.bundle(),
                    rationale=candidate.rationale,
                    metrics={"discovery_score": 1.0},
                    cost=0.02,
                    duration_seconds=0.01,
                )
            ],
            cost=CostSummary(optimizer=0.02, total=0.02, complete=self.cost_complete),
        )


async def _run_recording_pipeline(tmp_path: Path, backend: RecordingBackend) -> OptimizationReport:
    return await run_pipeline_async(
        train_path=backend.train_path,
        val_path=backend.val_path,
        optimizer_config_path=backend.config_path,
        prompt_path=backend.prompt_path,
        output_dir=tmp_path / "out",
        mode="fake",
        backend=backend,
        run_id="recording-run",
    )


@pytest.mark.asyncio
async def test_pipeline_evaluates_baselines_before_optimization_and_every_candidate(tmp_path: Path):
    backend = RecordingBackend(tmp_path)
    report = await run_pipeline_async(
        train_path=backend.train_path,
        val_path=backend.val_path,
        optimizer_config_path=backend.config_path,
        prompt_path=backend.prompt_path,
        output_dir=tmp_path / "out",
        mode="fake",
        backend=backend,
    )

    assert backend.calls == [
        ("evaluate", "baseline", "train"),
        ("evaluate", "baseline", "validation"),
        ("optimize",),
        ("evaluate", "candidate_safe", "train"),
        ("evaluate", "candidate_safe", "validation"),
    ]
    assert report.selected_candidate == "candidate_safe"


@pytest.mark.asyncio
async def test_gate_rejection_never_writes_source_even_when_requested(tmp_path: Path):
    backend = RecordingBackend(tmp_path, candidate_regresses=True)
    original = backend.prompt_path.read_bytes()

    report = await run_pipeline_async(
        train_path=backend.train_path,
        val_path=backend.val_path,
        optimizer_config_path=backend.config_path,
        prompt_path=backend.prompt_path,
        output_dir=tmp_path / "out",
        mode="fake",
        update_source=True,
        backend=backend,
    )

    assert report.selected_candidate is None
    assert report.writeback.status == "rejected"
    assert backend.prompt_path.read_bytes() == original


@pytest.mark.asyncio
async def test_incomplete_cost_rejects_when_budget_is_configured(tmp_path: Path):
    backend = RecordingBackend(tmp_path, cost_complete=False)
    report = await _run_recording_pipeline(tmp_path, backend)

    decision = report.gate_decisions[0]
    assert decision.accepted is False
    assert "cost_unavailable" in decision.reasons
```

Import `Any`, all schema classes used above, `DEFAULT_TRAIN`, `DEFAULT_VAL`, `DEFAULT_OPTIMIZER_CONFIG`, and `run_pipeline_async` at the top of the test. The backend method is named `optimize_candidates()` deliberately so it cannot be confused with the retained synchronous `SDKBackend.optimize()` compatibility wrapper.

- [ ] **Step 2: Run orchestration tests and confirm the shared pipeline is absent**

```powershell
python -m pytest examples/optimization/eval_optimize_loop/tests/test_pipeline_orchestration.py -v
```

Expected: collection fails because `run_pipeline_async` and `eval_loop.pipeline` do not exist.

- [ ] **Step 3: Add the request model and shared candidate loop**

Create `pipeline.py` with:

```python
@dataclass(frozen=True)
class PipelineRequest:
    train_path: Path
    validation_path: Path
    optimizer_config_path: Path
    output_dir: Path
    target_prompt_paths: dict[str, Path]
    gate_config: dict[str, Any]
    trace: bool
    update_source: bool
    mode: str
    run_id: str


async def execute_pipeline(
    request: PipelineRequest,
    *,
    evaluator: EvaluationBackend,
    optimizer: OptimizationBackend,
) -> OptimizationReport:
    started = time.perf_counter()
    prompt_snapshot = snapshot_prompt_files(request.target_prompt_paths)
    baseline_prompts = {
        name: item.content.decode("utf-8")
        for name, item in prompt_snapshot.files.items()
    }
    baseline_train = await evaluator.evaluate(
        prompt_id="baseline",
        prompts=baseline_prompts,
        dataset_path=request.train_path,
        split="train",
        trace=request.trace,
        artifact_dir=request.output_dir / "evaluator" / "baseline_train",
    )
    baseline_validation = await evaluator.evaluate(
        prompt_id="baseline",
        prompts=baseline_prompts,
        dataset_path=request.validation_path,
        split="validation",
        trace=request.trace,
        artifact_dir=request.output_dir / "evaluator" / "baseline_validation",
    )
    failure_summary = summarize_failures([baseline_train, baseline_validation])
    optimization = await optimizer.optimize_candidates(
        baseline_prompts=baseline_prompts,
        baseline_train=baseline_train,
        failure_summary=failure_summary,
        train_path=request.train_path,
        validation_path=request.validation_path,
        config_path=request.optimizer_config_path,
        artifact_dir=request.output_dir / "optimizer",
    )
    gate = AcceptanceGate(request.gate_config)
    candidate_records = []
    deltas = []
    decisions = []
    cumulative_cost = round(
        baseline_train.cost + baseline_validation.cost + optimization.cost.total,
        6,
    )
    for candidate in optimization.candidates:
        train_result = await evaluator.evaluate(
            prompt_id=candidate.candidate_id,
            prompts=candidate.bundle(),
            dataset_path=request.train_path,
            split="train",
            trace=request.trace,
            artifact_dir=request.output_dir / "evaluator" / f"{candidate.candidate_id}_train",
        )
        validation_result = await evaluator.evaluate(
            prompt_id=candidate.candidate_id,
            prompts=candidate.bundle(),
            dataset_path=request.validation_path,
            split="validation",
            trace=request.trace,
            artifact_dir=request.output_dir / "evaluator" / f"{candidate.candidate_id}_validation",
        )
        candidate_deltas = compute_case_deltas(
            candidate_id=candidate.candidate_id,
            baseline_train=baseline_train,
            baseline_validation=baseline_validation,
            candidate_train=train_result,
            candidate_validation=validation_result,
        )
        decision = gate.decide(
            candidate_id=candidate.candidate_id,
            baseline_train=baseline_train,
            baseline_validation=baseline_validation,
            candidate_train=train_result,
            candidate_validation=validation_result,
            deltas=candidate_deltas,
            cumulative_cost=cumulative_cost,
            cost_summary=optimization.cost,
        )
        candidate_records.append({
            "candidate": candidate,
            "train_result": train_result,
            "validation_result": validation_result,
        })
        deltas.extend(candidate_deltas)
        decisions.append(decision)
        cumulative_cost = decision.total_run_cost
    selected_candidate = select_candidate(candidate_records, decisions)
    duration_seconds = time.perf_counter() - started
    explicit_evaluator_cost = sum(
        result.cost
        for result in [baseline_train, baseline_validation]
        + [record[key] for record in candidate_records for key in ("train_result", "validation_result")]
    )
    run_cost = replace(
        optimization.cost,
        evaluator=round(optimization.cost.evaluator + explicit_evaluator_cost, 6),
        total=round(cumulative_cost, 6),
    )
    report = build_report(
        run={
            "run_id": request.run_id,
            "mode": request.mode,
            "trace": request.trace,
            "update_source": request.update_source,
        },
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_records=candidate_records,
        per_case_deltas=deltas,
        gate_decisions=decisions,
        selected_candidate=selected_candidate,
        audit={
            "duration_seconds": duration_seconds,
            "input_hashes": hash_pipeline_inputs(request, prompt_snapshot),
            "cost": to_jsonable(run_cost),
        },
    )
    return replace(report, rounds=optimization.rounds, cost_summary=run_cost)
```

Implement `hash_pipeline_inputs()` by hashing the train, validation, optimizer config, and snapshotted prompt bytes with SHA-256. Implement `select_candidate()` with the existing stable ordering: accepted candidates only, then highest validation score, then train score, then earliest candidate.

- [ ] **Step 4: Make the gate reject incomplete data and incomplete configured cost**

Add `cost_summary: CostSummary` to `AcceptanceGate.decide()`. Before score checks:

```python
for baseline, candidate in (
    (baseline_train, candidate_train),
    (baseline_validation, candidate_validation),
):
    baseline_ids = [case.case_id for case in baseline.cases]
    candidate_ids = [case.case_id for case in candidate.cases]
    if len(baseline_ids) != len(set(baseline_ids)) or len(candidate_ids) != len(set(candidate_ids)):
        reasons.append("reject: duplicate case IDs prevent a complete gate")
    elif set(baseline_ids) != set(candidate_ids):
        reasons.append("reject: baseline and candidate case IDs do not match")

if self.config.get("max_total_cost") is not None and not cost_summary.complete:
    reasons.append("reject: cost_unavailable for configured max_total_cost")
```

Treat `cumulative_cost` as the already-incurred baseline plus optimizer plus prior-candidate cost, and add only the current candidate's two explicit evaluation costs when calculating `total_run_cost`. `cost_summary` is passed separately to carry completeness; do not add its total a second time. Use the already-correct soft-to-hard condition: a candidate hard failure is new whenever the baseline case was not hard-failed.

- [ ] **Step 5: Make `run_pipeline.py` a compatibility wrapper**

Expose the full async signature and keep the sync API:

```python
async def run_pipeline_async(
    *,
    train_path: str | Path = DEFAULT_TRAIN,
    val_path: str | Path = DEFAULT_VAL,
    optimizer_config_path: str | Path = DEFAULT_OPTIMIZER_CONFIG,
    prompt_path: str | Path = DEFAULT_PROMPT,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    mode: str = "fake",
    fake_model: bool = True,
    fake_judge: bool = True,
    trace: bool = False,
    sdk_call_agent: str | None = None,
    update_source: bool = False,
    gate_config_path: str | Path | None = None,
    target_prompts: list[str] | None = None,
    run_id: str | None = None,
    backend: Any | None = None,
) -> OptimizationReport:
    request, selected_backend = build_pipeline_request_and_backend(
        train_path=train_path,
        val_path=val_path,
        optimizer_config_path=optimizer_config_path,
        prompt_path=prompt_path,
        output_dir=output_dir,
        mode=mode,
        fake_model=fake_model,
        fake_judge=fake_judge,
        trace=trace,
        sdk_call_agent=sdk_call_agent,
        update_source=update_source,
        gate_config_path=gate_config_path,
        target_prompts=target_prompts,
        run_id=run_id,
        backend=backend,
    )
    return await execute_pipeline(
        request,
        evaluator=selected_backend,
        optimizer=selected_backend,
    )


def run_pipeline(**kwargs: Any) -> OptimizationReport:
    if _has_running_loop():
        raise ValueError("run_pipeline() cannot run inside an active event loop; await run_pipeline_async()")
    return asyncio.run(run_pipeline_async(**kwargs))
```

Delete `_build_sdk_report()` and `_sdk_gate_decision()` after all callers move to `execute_pipeline()`.

Define `build_pipeline_request_and_backend()` immediately below the wrapper. Its implementation must perform these exact operations in order:

```python
def build_pipeline_request_and_backend(*, backend: Any | None, **options: Any) -> tuple[PipelineRequest, Any]:
    mode = str(options["mode"])
    if mode not in {"fake", "sdk"}:
        raise ValueError("mode must be 'fake' or 'sdk'")
    prompt_path = Path(options["prompt_path"])
    target_paths = _parse_target_prompt_paths(
        options.get("target_prompts"),
        default_prompt_path=prompt_path,
    )
    selected_run_id = validate_run_id(options.get("run_id") or create_run_id())
    if mode == "fake":
        config = load_optimizer_config(options["optimizer_config_path"])
        gate_config = asdict(config.gate)
        selected_backend = backend or FakeBackend(
            fake_model=options["fake_model"],
            fake_judge=options["fake_judge"],
        )
    else:
        if not options.get("sdk_call_agent") and backend is None:
            raise ValueError("--sdk-call-agent is required in sdk mode")
        gate_config = _load_sdk_gate_config(options.get("gate_config_path"))
        selected_backend = backend or SDKBackend(
            target_prompt_paths=target_paths,
            call_agent_path=options["sdk_call_agent"],
        )
    request = PipelineRequest(
        train_path=Path(options["train_path"]),
        validation_path=Path(options["val_path"]),
        optimizer_config_path=Path(options["optimizer_config_path"]),
        output_dir=Path(options["output_dir"]),
        target_prompt_paths=target_paths,
        gate_config=gate_config,
        trace=bool(options["trace"]),
        update_source=bool(options["update_source"]),
        mode=mode,
        run_id=selected_run_id,
    )
    return request, selected_backend
```

Keep `create_run_id()` and `validate_run_id()` small and deterministic at the boundary: generated IDs use UTC timestamp plus a random suffix; supplied IDs must satisfy the report artifact-name regex. Adapt constructor keywords to the final backend classes, but do not add mode-specific orchestration outside this factory.

- [ ] **Step 6: Persist audit before optional source write, then attach `WritebackResult`**

At the end of `execute_pipeline()`, call `prepare_run_artifacts(report, request.output_dir)` before any final write. It creates the run-specific temporary directory, writes the complete pre-write report and audit payload with `allow_nan=False`, and returns `RunArtifactPaths`. Then:

```python
artifact_paths = prepare_run_artifacts(report, request.output_dir)
if selected_candidate is None:
    writeback = WritebackResult(status="rejected", before_hashes=prompt_snapshot.hashes())
elif not request.update_source:
    writeback = WritebackResult(status="not_requested", before_hashes=prompt_snapshot.hashes())
else:
    selected = next(item for item in optimization.candidates if item.candidate_id == selected_candidate)
    writeback = commit_prompt_bundle(prompt_snapshot, selected.bundle())
report = replace(report, writeback=writeback)
finalize_run_artifacts(report, artifact_paths)
return report
```

Use `artifact_paths = prepare_run_artifacts(...)` for the value passed to finalization. Task 6 supplies the exact `RunArtifactPaths`, preparation, and finalization implementations. This order is the regression guarantee: audit preparation must succeed before `commit_prompt_bundle()` is reachable.

- [ ] **Step 7: Run orchestration, gate, fake, and SDK tests**

```powershell
python -m pytest examples/optimization/eval_optimize_loop/tests/test_pipeline_orchestration.py examples/optimization/eval_optimize_loop/tests/test_gate.py -v
python -m pytest examples/optimization/eval_optimize_loop/tests --tb=short
```

Expected: complete call order is enforced, rejected candidates never change source bytes, and no test expects `partial_applied`.

- [ ] **Step 8: Commit the shared pipeline**

```powershell
git add examples/optimization/eval_optimize_loop/eval_loop/pipeline.py examples/optimization/eval_optimize_loop/eval_loop/gate.py examples/optimization/eval_optimize_loop/eval_loop/report.py examples/optimization/eval_optimize_loop/run_pipeline.py examples/optimization/eval_optimize_loop/tests/test_pipeline_orchestration.py examples/optimization/eval_optimize_loop/tests/test_gate.py
git commit -m "refactor(examples): unify evaluation optimization pipeline"
```

### Task 6: Make report artifacts immutable and reproducible

**Files:**
- Modify: `examples/optimization/eval_optimize_loop/eval_loop/report.py`
- Create: `examples/optimization/eval_optimize_loop/tests/test_report_artifacts.py`
- Modify: `examples/optimization/eval_optimize_loop/tests/test_pipeline_fake_mode.py`

- [ ] **Step 1: Write failing run-isolation and sample-hash tests**

Create `test_report_artifacts.py`:

```python
def test_existing_run_id_is_never_overwritten(tmp_path: Path):
    first = run_pipeline(output_dir=tmp_path, mode="fake", trace=True, run_id="fixed-run")
    run_report = tmp_path / "runs" / "fixed-run" / "optimization_report.json"
    original = run_report.read_bytes()

    with pytest.raises(FileExistsError, match="fixed-run"):
        run_pipeline(output_dir=tmp_path, mode="fake", trace=True, run_id="fixed-run")

    assert run_report.read_bytes() == original
    assert first.audit["duration_seconds"] > 0


def test_committed_example_hashes_match_committed_inputs():
    root = Path("examples/optimization/eval_optimize_loop")
    payload = json.loads((root / "outputs/optimization_report.example.json").read_text(encoding="utf-8"))
    inputs = {
        "train": root / "data/train.evalset.json",
        "validation": root / "data/val.evalset.json",
        "optimizer": root / "data/optimizer.json",
        "prompt": root / "prompts/baseline_system_prompt.txt",
    }

    for name, path in inputs.items():
        assert payload["audit"]["input_hashes"][name] == hashlib.sha256(path.read_bytes()).hexdigest()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "C:\\\\Users\\\\" not in serialized
    assert "/Users/" not in serialized
    assert "/home/" not in serialized
```

- [ ] **Step 2: Run the tests and observe overwrite/stale-hash failures**

```powershell
python -m pytest examples/optimization/eval_optimize_loop/tests/test_report_artifacts.py -v
```

Expected: fake mode deletes/reuses its run directory, duration is zero, and committed example hashes mismatch.

- [ ] **Step 3: Implement run-specific temporary and final paths**

In `report.py`, add:

```python
@dataclass(frozen=True)
class RunArtifactPaths:
    output_dir: Path
    temporary: Path
    final: Path


def create_run_artifact_paths(output_dir: str | Path, run_id: str) -> RunArtifactPaths:
    root = Path(output_dir)
    final = root / "runs" / _safe_artifact_name(run_id)
    temporary = root / "runs" / f".{_safe_artifact_name(run_id)}.tmp"
    if final.exists() or temporary.exists():
        raise FileExistsError(f"run id already exists: {run_id}")
    temporary.mkdir(parents=True)
    return RunArtifactPaths(output_dir=root, temporary=temporary, final=final)


def finalize_run_directory(paths: RunArtifactPaths) -> None:
    paths.temporary.replace(paths.final)


def prepare_run_artifacts(report: OptimizationReport, output_dir: str | Path) -> RunArtifactPaths:
    paths = create_run_artifact_paths(output_dir, str(report.run["run_id"]))
    (paths.temporary / "optimization_report.json").write_text(
        report_to_json(report),
        encoding="utf-8",
    )
    (paths.temporary / "optimization_report.md").write_text(
        render_markdown(report),
        encoding="utf-8",
    )
    write_audit_artifacts(report, paths.temporary)
    return paths


def _atomic_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def finalize_run_artifacts(report: OptimizationReport, paths: RunArtifactPaths) -> None:
    json_value = report_to_json(report)
    markdown_value = render_markdown(report)
    _atomic_text(paths.temporary / "optimization_report.json", json_value)
    _atomic_text(paths.temporary / "optimization_report.md", markdown_value)
    _atomic_text(
        paths.temporary / "writeback.json",
        json.dumps(to_jsonable(report.writeback), indent=2, sort_keys=True, allow_nan=False) + "\n",
    )
    finalize_run_directory(paths)
    _atomic_text(paths.output_dir / "optimization_report.json", json_value)
    _atomic_text(paths.output_dir / "optimization_report.md", markdown_value)
```

Import `os`. Refactor `write_audit_artifacts()` so its second argument is the already-created run directory and it never creates, deletes, or reuses a run ID. Remove the fake-mode `shutil.rmtree(run_dir)` branch. Put SDK optimizer artifacts under the temporary run directory before finalization, not under a shared `<output_dir>/sdk_optimizer` directory. If preparation or finalization raises, do not reach source writeback; leave the temporary directory as failure evidence.

- [ ] **Step 4: Serialize complete audit artifacts**

Write baseline and every candidate split under `case_results/`; write `rounds/<round_id>.json`, `per_case_deltas.json`, `gate_decisions.json`, `writeback.json`, prompt bundles, diffs, input hashes, and config snapshot. Use `allow_nan=False` for every JSON write.

Normalize displayed paths with:

```python
def display_path(path: str | Path, *, repository_root: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(repository_root.resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()
```

Do not serialize the resolved contributor workspace path into committed examples.

- [ ] **Step 5: Record real duration and keep latest convenience copies**

Set `audit["duration_seconds"]` from the pipeline `perf_counter` measurement. After the run directory is finalized, atomically replace top-level `optimization_report.json` and `.md` with the finalized report contents. The immutable run directory remains authoritative.

- [ ] **Step 6: Run reporting tests**

```powershell
python -m pytest examples/optimization/eval_optimize_loop/tests/test_report_artifacts.py examples/optimization/eval_optimize_loop/tests/test_pipeline_fake_mode.py -v
```

Expected: a duplicate run ID raises without modifying the first report, runtime is positive, and all audit JSON is strict.

- [ ] **Step 7: Commit immutable audit artifacts**

```powershell
git add examples/optimization/eval_optimize_loop/eval_loop/report.py examples/optimization/eval_optimize_loop/tests/test_report_artifacts.py examples/optimization/eval_optimize_loop/tests/test_pipeline_fake_mode.py
git commit -m "fix(examples): make optimization audit reproducible"
```

### Task 7: Add independent acceptance evidence and real SDK integration

**Files:**
- Create: `examples/optimization/eval_optimize_loop/tests/fixtures/holdout_gate_cases.json`
- Create: `examples/optimization/eval_optimize_loop/tests/fixtures/attribution_cases.json`
- Create: `examples/optimization/eval_optimize_loop/tests/test_acceptance_thresholds.py`
- Create: `examples/optimization/eval_optimize_loop/tests/test_sdk_integration.py`

- [ ] **Step 1: Create an independent holdout decision fixture**

Create `holdout_gate_cases.json` with ten labeled scenarios:

```json
[
  {"id": "safe_gain", "expected": true, "train": [0, 1, 1], "candidate_train": [1, 1, 1], "validation": [0, 1, 1], "candidate_validation": [1, 1, 1], "protected": [], "max_drop": 0.0, "cost": 0.02, "budget": 1.0},
  {"id": "no_gain", "expected": false, "train": [1, 1, 1], "candidate_train": [1, 1, 1], "validation": [1, 1, 1], "candidate_validation": [1, 1, 1], "protected": [], "max_drop": 0.0, "cost": 0.02, "budget": 1.0},
  {"id": "overfit", "expected": false, "train": [0, 0, 1], "candidate_train": [1, 1, 1], "validation": [0, 1, 1], "candidate_validation": [1, 0, 0], "protected": [], "max_drop": 1.0, "cost": 0.02, "budget": 1.0},
  {"id": "validation_regression", "expected": false, "train": [1, 1, 1], "candidate_train": [1, 1, 1], "validation": [1, 1, 1], "candidate_validation": [1, 1, 0], "protected": [], "max_drop": 1.0, "cost": 0.02, "budget": 1.0},
  {"id": "protected_regression", "expected": false, "train": [0, 1, 1], "candidate_train": [1, 1, 1], "validation": [1, 0, 0], "candidate_validation": [0, 1, 1], "protected": ["v0"], "max_drop": 1.0, "cost": 0.02, "budget": 1.0},
  {"id": "new_hard_fail", "expected": false, "train": [0, 1, 1], "candidate_train": [1, 1, 1], "validation": [1, 0, 0], "candidate_validation": [0, 1, 1], "protected": [], "max_drop": 1.0, "cost": 0.02, "budget": 1.0},
  {"id": "soft_to_hard", "expected": false, "train": [0, 1, 1], "candidate_train": [1, 1, 1], "validation": [0.5, 0, 0], "candidate_validation": [0, 1, 1], "protected": [], "max_drop": 1.0, "cost": 0.02, "budget": 1.0},
  {"id": "single_case_drop", "expected": false, "train": [0, 1, 1], "candidate_train": [1, 1, 1], "validation": [0.7, 0, 0], "candidate_validation": [0.5, 1, 1], "protected": [], "max_drop": 0.1, "cost": 0.02, "budget": 1.0},
  {"id": "over_budget", "expected": false, "train": [0, 1, 1], "candidate_train": [1, 1, 1], "validation": [0, 1, 1], "candidate_validation": [1, 1, 1], "protected": [], "max_drop": 0.0, "cost": 1.1, "budget": 1.0},
  {"id": "safe_gain_with_soft_failure", "expected": true, "train": [0, 1, 1], "candidate_train": [1, 1, 1], "validation": [0.5, 0, 1], "candidate_validation": [0.5, 1, 1], "protected": ["v0"], "max_drop": 0.0, "cost": 0.02, "budget": 1.0}
]
```

Create `attribution_cases.json` with labels separated from evidence:

```json
[
  {"error_code": "json_parse_failure", "evidence": "JSON parser failed", "expected": "format_violation"},
  {"error_code": "required_key_missing", "evidence": "missing key status", "expected": "final_response_mismatch"},
  {"error_code": "exact_answer_mismatch", "evidence": "expected YES", "expected": "final_response_mismatch"},
  {"error_code": "tool_call_error", "evidence": "expected lookup", "expected": "tool_call_error"},
  {"error_code": "parameter_error", "evidence": "id mismatch", "expected": "parameter_error"},
  {"error_code": "missing_rubric_terms", "evidence": "missing latency", "expected": "llm_rubric_not_met"},
  {"error_code": "knowledge_recall_insufficient", "evidence": "missing doc-a", "expected": "knowledge_recall_insufficient"},
  {"error_code": "forbidden_pattern", "evidence": "forbidden JSON", "expected": "format_violation"}
]
```

- [ ] **Step 2: Implement threshold tests**

In `test_acceptance_thresholds.py`, load fixtures, build `EvalResult` objects without exposing `expected` to the gate or attribution function, and assert:

```python
def test_holdout_gate_decision_accuracy_is_at_least_eighty_percent():
    scenarios = _load_fixture("holdout_gate_cases.json")
    correct = 0
    for scenario in scenarios:
        decision = _decision_for_scenario(scenario)
        correct += decision.accepted == scenario["expected"]
    assert correct / len(scenarios) >= 0.80


def test_independent_attribution_accuracy_is_at_least_seventy_five_percent():
    scenarios = _load_fixture("attribution_cases.json")
    predictions = [attribute_failure(item["error_code"], item["evidence"]) for item in scenarios]
    correct = sum(prediction[0] == item["expected"] for prediction, item in zip(predictions, scenarios))
    assert correct / len(scenarios) >= 0.75
    assert all(prediction[1] and prediction[2] for prediction in predictions)


def test_fake_trace_pipeline_finishes_under_three_minutes(tmp_path: Path):
    started = time.perf_counter()
    report = run_pipeline(output_dir=tmp_path, mode="fake", trace=True, run_id="performance")
    elapsed = time.perf_counter() - started
    assert elapsed < 180
    assert 0 < report.audit["duration_seconds"] <= elapsed
```

Use these fixture helpers in the same file; only the final accuracy comparison reads the `expected` label:

```python
FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _eval(prompt_id: str, split: str, scores: list[float], *, cost: float = 0.0) -> EvalResult:
    cases = [
        CaseResult(
            case_id=f"{split[0]}{index}",
            split=split,
            score=float(score),
            passed=score >= 1.0,
            output=str(score),
            metrics={"holdout": float(score)},
            hard_failed=score == 0.0,
        )
        for index, score in enumerate(scores)
    ]
    return EvalResult(
        prompt_id=prompt_id,
        split=split,
        score=sum(scores) / len(scores),
        passed=all(case.passed for case in cases),
        cost=cost,
        cases=cases,
    )


def _decision_for_scenario(scenario: dict[str, Any]) -> GateDecision:
    baseline_train = _eval("baseline", "train", scenario["train"])
    baseline_validation = _eval("baseline", "validation", scenario["validation"])
    candidate_train = _eval("candidate", "train", scenario["candidate_train"])
    candidate_validation = _eval("candidate", "validation", scenario["candidate_validation"])
    deltas = compute_case_deltas(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_train=candidate_train,
        candidate_validation=candidate_validation,
    )
    gate = AcceptanceGate({
        "min_val_score_improvement": 0.01,
        "allow_new_hard_fail": False,
        "protected_case_ids": scenario["protected"],
        "max_score_drop_per_case": scenario["max_drop"],
        "max_total_cost": scenario["budget"],
    })
    return gate.decide(
        candidate_id="candidate",
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_train=candidate_train,
        candidate_validation=candidate_validation,
        deltas=deltas,
        cumulative_cost=float(scenario["cost"]),
        cost_summary=CostSummary(total=float(scenario["cost"]), complete=True),
    )
```

Import `Any`, `json`, all referenced schema/gate/report symbols, and `Path`. Here `scenario["cost"]` is supplied as already-incurred cost and the synthetic result costs are zero, matching the production rule that optimizer cost is counted once.

- [ ] **Step 3: Add a real SDK facade/evaluator integration test**

In `test_sdk_integration.py`, import the real `AgentOptimizer`, `AgentEvaluator`, `TargetPrompt`, and `GepaReflectiveOptimizer`. Monkeypatch only `GepaReflectiveOptimizer._call_gepa_optimize` with a deterministic GEPA result:

```python
class FakeGEPAResult:
    def __init__(self, baseline: dict[str, str], candidate: dict[str, str]):
        self.candidates = [baseline, candidate]
        self.val_aggregate_scores = [2 / 3, 1.0]
        self.parents = [[None], [0]]
        self.discovery_eval_counts = [0, 1]
        self.total_metric_calls = 6
        self.best_outputs_valset = None

    @property
    def best_idx(self) -> int:
        return 1


@pytest.mark.asyncio
async def test_sdk_pipeline_uses_real_facade_evaluator_and_post_gate_writeback(tmp_path: Path, monkeypatch):
    prompt_path = tmp_path / "system_prompt.txt"
    prompt_path.write_text("baseline prompt", encoding="utf-8")
    gate_path = tmp_path / "gate.json"
    gate_path.write_text('{"gate": {"max_total_cost": null}}', encoding="utf-8")
    baseline = {"system_prompt": "baseline prompt"}
    candidate = {
        "system_prompt": "baseline prompt\nUse strict JSON only when the user explicitly asks."
    }

    async def fake_call_gepa(self, **kwargs):
        return FakeGEPAResult(baseline, candidate)

    async def call_agent(query: str) -> str:
        prompt = prompt_path.read_text(encoding="utf-8")
        return deterministic_sdk_response(prompt, query)

    monkeypatch.setattr(GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa)
    module = ModuleType("issue91_sdk_call_agent")
    module.call_agent = call_agent
    monkeypatch.setitem(sys.modules, module.__name__, module)

    report = await run_pipeline_async(
        mode="sdk",
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        optimizer_config_path=DEFAULT_OPTIMIZER_CONFIG,
        prompt_path=prompt_path,
        output_dir=tmp_path / "out",
        sdk_call_agent="issue91_sdk_call_agent:call_agent",
        gate_config_path=gate_path,
        update_source=True,
        run_id="sdk-integration",
    )

    assert report.selected_candidate is not None
    assert all(record["train_result"].cases for record in report.candidates)
    assert all(record["validation_result"].cases for record in report.candidates)
    assert all(decision.gate_status == "applied" for decision in report.gate_decisions)
    assert report.writeback.status == "applied"
    assert prompt_path.read_text(encoding="utf-8") == candidate["system_prompt"]
```

Define the SDK response helper by routing only the prompt and user query through the oracle-free fake model. The dummy expectation proves the response path does not receive evaluator labels:

```python
def deterministic_sdk_response(prompt: str, query: str) -> str:
    case = EvalCase(
        case_id="sdk-runtime-query",
        split="runtime",
        input=query,
        expectation={"type": "runtime-only; must not be inspected"},
    )
    output, _, _ = FakeModel(seed=91).generate("sdk-runtime", prompt, case)
    return output
```

Import `DEFAULT_TRAIN`, `DEFAULT_VAL`, and `DEFAULT_OPTIMIZER_CONFIG` from `run_pipeline`. The null cost limit is intentional because this deterministic facade test cannot account for external LM billing; the separate gate tests cover configured complete and incomplete costs. Do not patch `AgentEvaluator`, `AgentOptimizer`, or `TargetPrompt`; patch only the external GEPA call shown above.

- [ ] **Step 4: Run acceptance and SDK integration tests**

```powershell
python -m pytest examples/optimization/eval_optimize_loop/tests/test_acceptance_thresholds.py examples/optimization/eval_optimize_loop/tests/test_sdk_integration.py -v
```

Expected: gate accuracy is at least 0.80, attribution accuracy is at least 0.75, performance is under 180 seconds, and the real SDK integration produces complete cases and post-gate writeback.

- [ ] **Step 5: Commit acceptance evidence**

```powershell
git add examples/optimization/eval_optimize_loop/tests/fixtures/holdout_gate_cases.json examples/optimization/eval_optimize_loop/tests/fixtures/attribution_cases.json examples/optimization/eval_optimize_loop/tests/test_acceptance_thresholds.py examples/optimization/eval_optimize_loop/tests/test_sdk_integration.py
git commit -m "test(examples): prove issue 91 acceptance thresholds"
```

### Task 8: Regenerate examples, update documentation, and run merge verification

**Files:**
- Modify: `examples/optimization/eval_optimize_loop/README.md`
- Modify: `examples/optimization/eval_optimize_loop/DESIGN.md`
- Modify: `examples/optimization/eval_optimize_loop/data/optimizer.json`
- Modify: `examples/optimization/eval_optimize_loop/outputs/optimization_report.example.json`
- Modify: `examples/optimization/eval_optimize_loop/outputs/optimization_report.example.md`
- Modify: `.gitignore`
- Modify: `pyproject.toml`

- [ ] **Step 1: Update user documentation to match the unified semantics**

Document these exact guarantees in README and the 300–500 字 design summary:

- fake and SDK share baseline, candidate re-evaluation, delta, and gate semantics;
- SDK optimization always uses `update_source=False` internally;
- `--update-source` writes only the selected candidate after audit preparation and full gate acceptance;
- configured cost gate rejects when total cost is incomplete;
- fake model reads only user input and prompt;
- run directories are immutable and run-specific;
- SDK mode reports real per-case metrics and trace availability.

Remove every statement describing `partial_applied`, aggregate-only accepted candidates, fixed zero duration, or shared SDK artifact directories.

- [ ] **Step 2: Regenerate committed example outputs from the final fake pipeline**

Run:

```powershell
$output = Join-Path $env:TEMP 'issue91-final-example'
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode fake --trace --run-id example --output-dir $output
Copy-Item -LiteralPath (Join-Path $output 'optimization_report.json') -Destination 'examples/optimization/eval_optimize_loop/outputs/optimization_report.example.json' -Force
Copy-Item -LiteralPath (Join-Path $output 'optimization_report.md') -Destination 'examples/optimization/eval_optimize_loop/outputs/optimization_report.example.md' -Force
```

Expected: selected candidate is the safe candidate, the overfit candidate is rejected, duration is positive, input paths are repository-relative, and hashes match current committed inputs.

- [ ] **Step 3: Run example formatting and focused verification**

```powershell
python -m compileall -q examples/optimization/eval_optimize_loop
python -m yapf --diff -r examples/optimization/eval_optimize_loop
python -m flake8 examples/optimization/eval_optimize_loop
python -m pytest examples/optimization/eval_optimize_loop/tests --tb=short
git diff --check
```

Expected: every command exits 0, YAPF emits no diff, and the example test suite has zero failures.

- [ ] **Step 4: Run repository CI-equivalent tests and build**

```powershell
python -m pytest --cov=trpc_agent_sdk --cov-report=xml --cov-report=term --cov-fail-under=80 tests/
python -m build
python -c "import trpc_agent_sdk; print('Import OK')"
```

Expected: repository tests pass with coverage at least 80%, package build succeeds, and the import command prints `Import OK`.

- [ ] **Step 5: Verify the explicit Issue #91 merge checklist**

Run:

```powershell
python -m pytest examples/optimization/eval_optimize_loop/tests/test_acceptance_thresholds.py -v
rg -n "partial_applied|duration_seconds.: 0\.0|C:\\\\Users\\\\|/Users/|/home/" examples/optimization/eval_optimize_loop
git status --short
git diff --stat origin/main...HEAD
```

Expected: threshold tests pass; `rg` finds no stale partial-gate, zero-duration, or personal-path text in runtime/example artifacts; status lists only intentional final changes; diff scope remains limited to Issue #91 and the two superpowers documents.

- [ ] **Step 6: Commit documentation and generated artifacts**

```powershell
git add .gitignore pyproject.toml examples/optimization/eval_optimize_loop/README.md examples/optimization/eval_optimize_loop/DESIGN.md examples/optimization/eval_optimize_loop/data/optimizer.json examples/optimization/eval_optimize_loop/outputs/optimization_report.example.json examples/optimization/eval_optimize_loop/outputs/optimization_report.example.md
git commit -m "docs(examples): finalize issue 91 closed loop"
```

- [ ] **Step 7: Perform the final branch review before publishing**

```powershell
git log --oneline origin/main..HEAD
git diff --check origin/main...HEAD
git status --short --branch
```

Expected: commits are task-scoped, diff check exits 0, the working tree is clean, and the branch contains no unrelated files. Use `superpowers:requesting-code-review` before push or PR update.
