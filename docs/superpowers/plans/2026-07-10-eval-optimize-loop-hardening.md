# Eval Optimize Loop Hidden-Test Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining issue-compliance and hidden-test gaps in the evaluation/optimization example while preserving the verified fake, trace, and real-API behavior.

**Architecture:** Keep the issue-facing pipeline in `run_pipeline.py`, but make its three decision boundaries explicit: normalized evaluator evidence, total gate decisions, and a strict report contract. Reuse `AgentEvaluator`, `AgentOptimizer`, `TargetPrompt`, and native optimizer round records; do not create a second optimization engine or expose final-validation gold to optimization.

**Tech Stack:** Python 3.10+, asyncio, tRPC-Agent `AgentEvaluator`/`AgentOptimizer`, Pydantic-backed SDK result models, JSON Schema Draft 2020-12, pytest, fake fixtures, trace replay, OpenAI-compatible real API.

## Global Constraints

- Fake and trace modes require no API variables and must finish in less than 180 seconds.
- Online mode remains opt-in through `TRPC_AGENT_API_KEY`, `TRPC_AGENT_BASE_URL`, and `TRPC_AGENT_MODEL_NAME`.
- `train.evalset.json`, `optimizer_dev.evalset.json`, and `val.evalset.json` must be path-distinct and case-id/content-disjoint where their roles require isolation.
- Final-validation gold may be used only for baseline scoring, final candidate scoring, and the product gate.
- Generated outputs stay under `runs/` or a caller-provided output directory.
- Reports remain JSON-first and must not contain API keys, authorization headers, model thoughts, or unredacted provider URLs.
- Keep the existing source prompts unchanged; candidate prompts are written only as run artifacts unless a caller explicitly opts into source updates.
- Do not silence repository-wide warnings or skip unrelated tests merely to make this issue appear green.
- Every task ends with focused verification and one scoped commit.

---

## Verified Starting Point

The following evidence was reproduced on 2026-07-10 before writing this plan:

- Focused example suite: 28 passed, 1 opt-in online test skipped.
- Entire `tests/evaluation` suite: exit code 0, with the same opt-in online skip.
- Fake run: accepted `candidate_local_patch`, duration 4.58 seconds.
- Trace run: accepted `candidate_local_patch`, duration 4.58 seconds, no API required.
- Real API weak-baseline run: baseline validation 0.666667, candidate validation 1.0, accepted, 176.3446 seconds, 37 model calls.
- Real API default-prompt run: baseline and candidate validation 1.0, rejected for no improvement, 95.69541 seconds, 36 model calls.
- Full repository `pytest -q`: non-zero on Windows because of existing POSIX path, permission, shell-command, symlink, dependency-version, and one LangGraph `Interrupt(id=...)` incompatibility; therefore “all tests pass” is not currently a true repository-wide statement.

The issue-specific implementation is functional, but the reproduced hidden-style failures are:

- malformed `tool` values can crash failure attribution;
- `arguments: null` and `arguments: []` are mislabeled as `knowledge_gap`;
- a candidate can omit validation cases and still be accepted;
- an unexpected candidate case raises `KeyError`;
- non-finite scores can be accepted;
- equality at an exact configured delta boundary is accepted even though the issue specifies improvement strictly greater than the threshold;
- case deltas lack explicit new-pass/new-fail/improved/regressed classification;
- the schema accepts empty case results, empty deltas, empty case-delta objects, and out-of-range attribution coverage;
- candidate-level cost/duration/reproducibility fields are absent;
- the Design Notes section is 976 non-whitespace characters, outside the requested 300-500-character range;
- warning suppression and a global DeepSeek log-level downgrade hide symptoms instead of fixing or recording them;
- the checked-in sample has a trailing blank line that fails `git diff --check`.

---

### Task 1: Make Failure Attribution Total and Type-Safe

**Files:**
- Modify: `examples/optimization/eval_optimize_loop/run_pipeline.py:420`
- Modify: `tests/evaluation/test_eval_optimize_loop_example.py:494`

**Interfaces:**
- Consumes: final response strings and per-case metric dictionaries.
- Produces: `attribute_failure_case(...) -> {"root_cause": str, "reasons": list[str]}` for every input without raising.

- [ ] **Step 1: Add failing malformed-structure tests**

```python
@pytest.mark.parametrize(
    ("actual_text", "expected_root"),
    [
        (
            '{"route":"faq","tool":"none","reason":"bad shape"}',
            "tool_call_error",
        ),
        (
            '{"route":"faq","tool":{"name":"none","arguments":null},"reason":"bad args"}',
            "parameter_error",
        ),
        (
            '{"route":"faq","tool":{"name":"none","arguments":[]},"reason":"bad args"}',
            "parameter_error",
        ),
        (
            '{"route":"faq","tool":{"name":"none"},"reason":"missing args"}',
            "parameter_error",
        ),
    ],
)
def test_failure_attribution_is_total_for_malformed_tool_shapes(
    actual_text: str,
    expected_root: str,
):
    module = load_pipeline_module()
    result = module.attribute_failure_case(
        actual_text=actual_text,
        expected_text=(
            '{"route":"faq","tool":{"name":"none","arguments":{}},'
            '"reason":"expected"}'
        ),
        error_message=None,
        metrics={ROUTE_TOOL_ARGS_METRIC: {"passed": False}},
    )
    assert result["root_cause"] == expected_root
    assert result["reasons"]
```

- [ ] **Step 2: Run the new test and verify the current crash/misclassification**

Run:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py::test_failure_attribution_is_total_for_malformed_tool_shapes -q
```

Expected: FAIL with either `AttributeError: 'str' object has no attribute 'get'` or `knowledge_gap != parameter_error`.

- [ ] **Step 3: Replace truthiness-based tool/argument parsing with type checks**

```python
def _failed_metric_names(metrics: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(name for name, metric in metrics.items() if _metric_failed(metric))


def _metric_failure_root(failed_metric_names: list[str]) -> tuple[str, str]:
    rubric = [name for name in failed_metric_names if "rubric" in name or name.startswith("llm_")]
    if rubric:
        return "rubric_failed", "rubric metric failed: " + ", ".join(rubric)
    knowledge = [
        name
        for name in failed_metric_names
        if any(token in name.lower() for token in ("knowledge", "retrieval", "recall", "ground"))
    ]
    if knowledge:
        return "knowledge_gap", "knowledge metric failed: " + ", ".join(knowledge)
    return "metric_failed", "content metric failed: " + ", ".join(failed_metric_names)
```

In `attribute_failure_case`, replace the current `actual_tool = actual.get("tool") or {}` block with:

```python
    actual_tool = actual.get("tool")
    expected_tool = expected.get("tool")
    if not isinstance(expected_tool, dict):
        return {
            "root_cause": "runtime_error",
            "reasons": ["expected final response has a non-object tool field"],
        }
    if str(actual.get("route", "")) != str(expected.get("route", "")):
        return {
            "root_cause": "final_response_mismatch",
            "reasons": [
                f"actual route {actual.get('route')!r} did not match "
                f"expected route {expected.get('route')!r}"
            ],
        }
    if not isinstance(actual_tool, dict):
        return {
            "root_cause": "tool_call_error",
            "reasons": ["actual tool must be a JSON object"],
        }
    if str(actual_tool.get("name", "")) != str(expected_tool.get("name", "")):
        return {
            "root_cause": "tool_call_error",
            "reasons": [
                f"actual tool {actual_tool.get('name')!r} did not match "
                f"expected tool {expected_tool.get('name')!r}"
            ],
        }
    actual_arguments = actual_tool.get("arguments", _MISSING)
    expected_arguments = expected_tool.get("arguments", _MISSING)
    if not isinstance(expected_arguments, dict):
        return {
            "root_cause": "runtime_error",
            "reasons": ["expected tool arguments must be a JSON object"],
        }
    if not isinstance(actual_arguments, dict):
        return {
            "root_cause": "parameter_error",
            "reasons": ["actual tool arguments must be a JSON object"],
        }
    if actual_arguments != expected_arguments:
        return {
            "root_cause": "parameter_error",
            "reasons": ["tool arguments did not match expected arguments"],
        }

    failed_metric_names = _failed_metric_names(metrics)
    if failed_metric_names:
        root_cause, reason = _metric_failure_root(failed_metric_names)
        return {"root_cause": root_cause, "reasons": [reason]}
    return {
        "root_cause": "metric_failed",
        "reasons": ["case failed without a reported failed metric"],
    }
```

Add `"metric_failed"` to `TAXONOMY`.

- [ ] **Step 4: Run canonical and adversarial attribution tests**

Run:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "failure_attribution or route_tool_argument" -q
```

Expected: PASS; every failed case has one taxonomy value and at least one non-empty reason.

- [ ] **Step 5: Commit the isolated attribution fix**

```powershell
git add examples/optimization/eval_optimize_loop/run_pipeline.py tests/evaluation/test_eval_optimize_loop_example.py
git commit -m "fix: harden optimization failure attribution"
```

---

### Task 2: Make Gate Decisions Total, Strict, and Fail-Closed

**Files:**
- Modify: `examples/optimization/eval_optimize_loop/run_pipeline.py:907`
- Modify: `tests/evaluation/test_eval_optimize_loop_example.py:400`

**Interfaces:**
- Consumes: baseline and candidate evaluation summaries.
- Produces: a gate result that never accepts malformed evidence and never raises for case-set mismatch.

- [ ] **Step 1: Add a table-driven gate adversarial suite**

```python
def _gate_summary(
    score: float,
    cases: list[dict[str, Any]],
    *,
    metric_passed: bool = True,
) -> dict[str, Any]:
    return {
        "score": score,
        "metrics": {ROUTE_TOOL_ARGS_METRIC: {"passed": metric_passed}},
        "case_results": cases,
    }


def test_gate_fails_closed_for_boundary_and_invalid_evidence():
    module = load_pipeline_module()
    baseline = _gate_summary(
        0.25,
        [
            {"case_id": "a", "score": 0.0, "passed": False, "tags": []},
            {"case_id": "b", "score": 0.5, "passed": True, "tags": ["critical"]},
        ],
    )
    valid_candidate = _gate_summary(
        0.75,
        [
            {"case_id": "a", "score": 1.0, "passed": True, "tags": []},
            {"case_id": "b", "score": 0.5, "passed": True, "tags": ["critical"]},
        ],
    )

    exact_boundary = module.apply_gate(
        candidate_id="boundary",
        baseline_val=baseline,
        candidate_val=valid_candidate,
        gate_config={
            "min_validation_delta": 0.5,
            "required_metrics": [ROUTE_TOOL_ARGS_METRIC],
        },
        duration_seconds=1.0,
        cost_usd=0.0,
    )
    assert exact_boundary["accepted"] is False

    missing_case = copy.deepcopy(valid_candidate)
    missing_case["case_results"] = missing_case["case_results"][:1]
    missing = module.apply_gate(
        candidate_id="missing",
        baseline_val=baseline,
        candidate_val=missing_case,
        gate_config={"required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
        duration_seconds=1.0,
        cost_usd=0.0,
    )
    assert missing["accepted"] is False
    assert missing["missing_case_ids"] == ["b"]

    extra_case = copy.deepcopy(valid_candidate)
    extra_case["case_results"].append(
        {"case_id": "c", "score": 1.0, "passed": True, "tags": []}
    )
    extra = module.apply_gate(
        candidate_id="extra",
        baseline_val=baseline,
        candidate_val=extra_case,
        gate_config={"required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
        duration_seconds=1.0,
        cost_usd=0.0,
    )
    assert extra["accepted"] is False
    assert extra["unexpected_case_ids"] == ["c"]

    non_finite = copy.deepcopy(valid_candidate)
    non_finite["score"] = float("nan")
    invalid = module.apply_gate(
        candidate_id="nan",
        baseline_val=baseline,
        candidate_val=non_finite,
        gate_config={"required_metrics": [ROUTE_TOOL_ARGS_METRIC]},
        duration_seconds=1.0,
        cost_usd=0.0,
    )
    assert invalid["accepted"] is False
    assert "finite" in " ".join(invalid["reasons"])
```

- [ ] **Step 2: Verify the suite fails on all reproduced boundary defects**

Run:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py::test_gate_fails_closed_for_boundary_and_invalid_evidence -q
```

Expected: FAIL because the current gate accepts a missing case and NaN, raises on an extra case, and accepts an exact 0.5 boundary.

- [ ] **Step 3: Add finite-number and case-index helpers**

```python
import math


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _index_gate_cases(
    evaluation: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    cases = evaluation.get("case_results")
    if not isinstance(cases, list):
        return {}, ["case_results must be an array"]
    indexed: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    for position, case in enumerate(cases):
        if not isinstance(case, dict) or not str(case.get("case_id", "")).strip():
            issues.append(f"case_results[{position}] has no case_id")
            continue
        case_id = str(case["case_id"])
        if case_id in indexed:
            issues.append(f"duplicate case_id: {case_id}")
            continue
        indexed[case_id] = case
    return indexed, issues
```

- [ ] **Step 4: Replace gate preconditions and threshold comparison**

At the start of `apply_gate`, build both indexes, append all validation issues to `reasons`, and set `accepted = False` when any issue exists. Compute:

```python
    baseline_score = _finite_float(baseline_val.get("score"))
    candidate_score = _finite_float(candidate_val.get("score"))
    validation_delta = (
        None
        if baseline_score is None or candidate_score is None
        else candidate_score - baseline_score
    )
    if validation_delta is None:
        accepted = False
        reasons.append("baseline and candidate validation scores must be finite numbers")
    else:
        min_delta = float(gate_config.get("min_validation_delta", 0.0))
        if validation_delta <= min_delta:
            accepted = False
            reasons.append(
                f"validation score improvement {validation_delta:.4f} "
                f"must be greater than required {min_delta:.4f}"
            )

    baseline_ids = set(baseline_by_id)
    candidate_ids = set(candidate_by_id)
    missing_case_ids = sorted(baseline_ids - candidate_ids)
    unexpected_case_ids = sorted(candidate_ids - baseline_ids)
    if missing_case_ids:
        accepted = False
        reasons.append("candidate omitted validation case(s): " + ", ".join(missing_case_ids))
    if unexpected_case_ids:
        accepted = False
        reasons.append("candidate introduced unknown validation case(s): " + ", ".join(unexpected_case_ids))
```

Only compute hard-fail and critical-regression checks over `sorted(baseline_ids & candidate_ids)`. Normalize tags with `{str(tag).lower() for tag in candidate_case.get("tags", [])}`.

Return `missing_case_ids`, `unexpected_case_ids`, and nullable `validation_delta` in the gate result.

- [ ] **Step 5: Run the complete gate test set**

Run:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "gate or regression or cost_budget" -q
```

Expected: PASS for improvement, no-op, aggregate regression, critical regression, hard fail, required metric, cost, duration, exact threshold, malformed case set, and non-finite score cases.

- [ ] **Step 6: Commit the gate hardening**

```powershell
git add examples/optimization/eval_optimize_loop/run_pipeline.py tests/evaluation/test_eval_optimize_loop_example.py
git commit -m "fix: make optimization gates fail closed"
```

---

### Task 3: Add Explicit Case-Delta Classes and Key Traces

**Files:**
- Modify: `examples/optimization/eval_optimize_loop/run_pipeline.py:573`
- Modify: `examples/optimization/eval_optimize_loop/run_pipeline.py:907`
- Modify: `tests/evaluation/test_eval_optimize_loop_example.py:225`

**Interfaces:**
- Produces: each evaluation case with `expected_text` and `key_trace`.
- Produces: each case delta with pass-state transitions and one stable `change_type`.

- [ ] **Step 1: Add failing tests for all required delta classes**

```python
def test_case_deltas_classify_pass_fail_and_score_transitions():
    module = load_pipeline_module()
    baseline = {
        "case_results": [
            {"case_id": "new_pass", "score": 0.0, "passed": False, "actual_text": "b1"},
            {"case_id": "new_fail", "score": 1.0, "passed": True, "actual_text": "b2"},
            {"case_id": "up", "score": 0.4, "passed": True, "actual_text": "b3"},
            {"case_id": "down", "score": 0.8, "passed": True, "actual_text": "b4"},
            {"case_id": "same", "score": 1.0, "passed": True, "actual_text": "b5"},
        ]
    }
    candidate = {
        "case_results": [
            {"case_id": "new_pass", "score": 1.0, "passed": True, "actual_text": "c1", "root_cause": "", "reasons": []},
            {"case_id": "new_fail", "score": 0.0, "passed": False, "actual_text": "c2", "root_cause": "format_error", "reasons": ["bad"]},
            {"case_id": "up", "score": 0.6, "passed": True, "actual_text": "c3", "root_cause": "", "reasons": []},
            {"case_id": "down", "score": 0.6, "passed": True, "actual_text": "c4", "root_cause": "", "reasons": []},
            {"case_id": "same", "score": 1.0, "passed": True, "actual_text": "c5", "root_cause": "", "reasons": []},
        ]
    }
    by_id = {
        item["case_id"]: item
        for item in module.build_case_deltas(baseline, candidate)
    }
    assert by_id["new_pass"]["change_type"] == "new_pass"
    assert by_id["new_fail"]["change_type"] == "new_fail"
    assert by_id["up"]["change_type"] == "score_improved"
    assert by_id["down"]["change_type"] == "score_regressed"
    assert by_id["same"]["change_type"] == "unchanged"
    assert by_id["new_fail"]["baseline_passed"] is True
    assert by_id["new_fail"]["candidate_passed"] is False
```

Extend the fake report test with:

```python
    first_case = report["baseline"]["validation"]["case_results"][0]
    assert first_case["expected_text"]
    assert first_case["key_trace"]["invocation_id"]
    assert first_case["key_trace"]["actual_final_response"] == first_case["actual_text"]
    assert first_case["key_trace"]["expected_final_response"] == first_case["expected_text"]
```

- [ ] **Step 2: Run the tests and verify missing fields**

Run:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "case_deltas_classify or generates_complete_report" -q
```

Expected: FAIL on missing `change_type`, pass-state fields, `expected_text`, and `key_trace`.

- [ ] **Step 3: Add a deterministic classifier**

```python
def classify_case_delta(before: dict[str, Any], after: dict[str, Any]) -> str:
    if not bool(before.get("passed")) and bool(after.get("passed")):
        return "new_pass"
    if bool(before.get("passed")) and not bool(after.get("passed")):
        return "new_fail"
    delta = float(after.get("score", 0.0)) - float(before.get("score", 0.0))
    if delta > 0:
        return "score_improved"
    if delta < 0:
        return "score_regressed"
    return "unchanged"
```

Replace `build_case_deltas` with a union-based implementation so a rejected
case-set mismatch is still reportable:

```python
def build_case_deltas(
    baseline_val: dict[str, Any],
    candidate_val: dict[str, Any],
) -> list[dict[str, Any]]:
    baseline_by_id = {
        str(case["case_id"]): case
        for case in baseline_val.get("case_results", [])
        if isinstance(case, dict) and case.get("case_id")
    }
    candidate_by_id = {
        str(case["case_id"]): case
        for case in candidate_val.get("case_results", [])
        if isinstance(case, dict) and case.get("case_id")
    }
    deltas: list[dict[str, Any]] = []
    for case_id in sorted(set(baseline_by_id) | set(candidate_by_id)):
        before = baseline_by_id.get(case_id)
        case = candidate_by_id.get(case_id)
        if before is None:
            deltas.append({
                "case_id": case_id,
                "baseline_score": None,
                "candidate_score": case.get("score"),
                "baseline_passed": None,
                "candidate_passed": bool(case.get("passed")),
                "delta": None,
                "change_type": "unexpected_candidate",
                "baseline_actual_text": "",
                "candidate_actual_text": case.get("actual_text", ""),
                "root_cause": "runtime_error",
                "reasons": ["candidate introduced an unknown validation case"],
            })
            continue
        if case is None:
            deltas.append({
                "case_id": case_id,
                "baseline_score": before.get("score"),
                "candidate_score": None,
                "baseline_passed": bool(before.get("passed")),
                "candidate_passed": None,
                "delta": None,
                "change_type": "missing_candidate",
                "baseline_actual_text": before.get("actual_text", ""),
                "candidate_actual_text": "",
                "root_cause": "runtime_error",
                "reasons": ["candidate omitted a baseline validation case"],
            })
            continue
        delta = round(float(case["score"]) - float(before["score"]), 6)
        deltas.append({
            "case_id": case_id,
            "baseline_score": before["score"],
            "candidate_score": case["score"],
            "baseline_passed": bool(before["passed"]),
            "candidate_passed": bool(case["passed"]),
            "delta": delta,
            "change_type": classify_case_delta(before, case),
            "baseline_actual_text": before.get("actual_text", ""),
            "candidate_actual_text": case.get("actual_text", ""),
            "root_cause": case.get("root_cause", ""),
            "reasons": case.get("reasons", []),
        })
    return deltas
```

- [ ] **Step 4: Add the key trace at evaluator-summary time**

When constructing each case result in `summarize_evaluate_result`, include:

```python
            "expected_text": expected_text,
            "key_trace": {
                "invocation_id": str(
                    case_by_id[eval_id]["conversation"][0].get("invocation_id", "")
                ),
                "actual_final_response": actual_text,
                "expected_final_response": expected_text,
                "error_message": error_message,
            },
```

For the no-run branch, use the same shape with an empty actual response and `"AgentEvaluator returned no run for case"` as the error. Do not include chain-of-thought or provider headers.

- [ ] **Step 5: Verify report and Markdown expose the same classifications**

Update `render_markdown` so every winner case line appends `change_type`. Then run:

```powershell
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode fake --output-dir runs/plan_verify --run-id delta_trace
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "case_delta or key_trace or fake_mode" -q
```

Expected: PASS; JSON and Markdown both distinguish new pass, new fail, score improvement, score regression, and unchanged.

- [ ] **Step 6: Commit the delta and trace contract**

```powershell
git add examples/optimization/eval_optimize_loop/run_pipeline.py tests/evaluation/test_eval_optimize_loop_example.py
git commit -m "feat: classify optimization case deltas"
```

---

### Task 4: Complete Candidate and Optimizer-Round Audit Data

**Files:**
- Modify: `examples/optimization/eval_optimize_loop/run_pipeline.py:1026`
- Modify: `examples/optimization/eval_optimize_loop/run_pipeline.py:1219`
- Modify: `examples/optimization/eval_optimize_loop/run_pipeline.py:1683`
- Modify: `tests/evaluation/test_eval_optimize_loop_example.py:638`

**Interfaces:**
- Produces: `candidate.audit` with seed, duration, cost status, and config digest.
- Produces: `optimization_rounds` containing native round prompt artifacts, metrics, decision, cost, and duration.

- [ ] **Step 1: Add failing candidate-audit and artifact-existence tests**

```python
def _assert_candidate_audit(candidate: dict[str, Any], seed: int) -> None:
    audit = candidate["audit"]
    assert audit["seed"] == seed
    assert audit["duration_seconds"] >= 0
    assert audit["cost"]["currency"] == "USD"
    assert audit["config_sha256"]
    assert len(audit["config_sha256"]) == 64


@pytest.mark.asyncio
async def test_fake_mode_audits_each_candidate_independently(tmp_path: Path):
    module = load_pipeline_module()
    run_dir = await module.run_fake_or_trace(
        mode="fake",
        seed=7,
        output_dir=tmp_path,
        run_id="candidate_audit",
    )
    report = load_report(run_dir / "optimization_report.json")
    for candidate in report["candidates"]:
        _assert_candidate_audit(candidate, 7)
        assert Path(candidate["artifacts"]["prompt_dir"]).is_dir()
        assert Path(candidate["artifacts"]["prompt_patch"]).is_file()
```

In the mocked online test, assert:

```python
    assert report["optimization_rounds"] == []
    for name, value in report["artifacts"].items():
        if name.startswith("native_") and value:
            assert Path(value).exists(), name
```

- [ ] **Step 2: Verify candidate audit is currently absent**

Run:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "audits_each_candidate or construct_optimizer_call" -q
```

Expected: FAIL on missing `candidate.audit` and on the nonexistent `native_rounds_dir` when zero rounds ran.

- [ ] **Step 3: Add reproducibility digest helpers**

```python
def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_candidate_audit(
    *,
    seed: int,
    duration_seconds: float,
    cost_usd: float | None,
    optimizer_config: Path,
) -> dict[str, Any]:
    return {
        "seed": seed,
        "duration_seconds": round(duration_seconds, 6),
        "cost": {
            "currency": "USD",
            "estimated": cost_usd,
            "known": cost_usd is not None,
        },
        "config_path": str(optimizer_config),
        "config_sha256": sha256_file(optimizer_config),
    }
```

Extend `build_candidate_report` with required `seed` and `optimizer_config` parameters and add `"audit": build_candidate_audit(...)`.

- [ ] **Step 4: Measure offline candidates independently**

Set `candidate_started = time.perf_counter()` immediately before each candidate's train evaluation. Pass `time.perf_counter() - candidate_started` to `build_candidate_report`; do not use cumulative pipeline duration as candidate duration.

- [ ] **Step 5: Normalize native optimizer rounds**

```python
def write_optimizer_round_artifacts(
    *,
    run_dir: Path,
    rounds: list[Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for round_record in rounds:
        round_id = int(round_record.round)
        round_dir = run_dir / "prompts" / f"optimizer_round_{round_id:03d}"
        round_dir.mkdir(parents=True, exist_ok=True)
        prompt_paths: dict[str, str] = {}
        prompt_hashes: dict[str, str] = {}
        for name, content in sorted(round_record.candidate_prompts.items()):
            prompt_path = round_dir / f"{name}.md"
            prompt_path.write_text(content, encoding="utf-8")
            prompt_paths[name] = str(prompt_path)
            prompt_hashes[name] = sha256_text(content)
        records.append({
            "round": round_id,
            "optimized_field_names": list(round_record.optimized_field_names),
            "prompt_paths": prompt_paths,
            "prompt_sha256": prompt_hashes,
            "validation_pass_rate": float(round_record.validation_pass_rate),
            "metric_breakdown": dict(round_record.metric_breakdown),
            "accepted": bool(round_record.accepted),
            "decision_reason": (
                round_record.acceptance_reason
                or round_record.skip_reason
                or round_record.error_message
                or "optimizer reported no reason"
            ),
            "failed_case_ids": list(round_record.failed_case_ids),
            "cost_usd": float(round_record.round_llm_cost),
            "token_usage": dict(round_record.round_token_usage),
            "duration_seconds": float(round_record.duration_seconds),
        })
    return records
```

Add `optimization_rounds` to online reports and `[]` to fake/trace reports.

- [ ] **Step 6: List only native artifacts that exist**

Build the online artifact dictionary with a helper:

```python
def existing_artifact(path: Path) -> str:
    return str(path) if path.exists() else ""
```

Use it for `native_result_json`, `native_summary_txt`, `native_rounds_dir`, `native_baseline_prompts_dir`, `native_best_prompts_dir`, and `native_config_snapshot_json`. Preserve the report JSON/Markdown destination paths because they are created by `write_report` immediately after validation.

- [ ] **Step 7: Preserve an auditable report when native optimization returns FAILED**

Merge source prompts before final evaluation:

```python
    source_prompt_texts = {name: text for name, (_, text) in source_prompts.items()}
    best_prompt_texts = {
        **source_prompt_texts,
        **dict(getattr(result, "best_prompts", {}) or {}),
    }
```

Use `best_prompt_texts` for the candidate evaluator and prompt artifacts. If `result.status != "SUCCEEDED"`, reject the candidate and include `result.error_message` in the gate reasons and `online_result`; still write the complete report.

- [ ] **Step 8: Run audit tests and commit**

Run:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "audit or prompt_artifacts or online_mode" -q
```

Expected: PASS, including zero-round and failed-optimizer cases.

```powershell
git add examples/optimization/eval_optimize_loop/run_pipeline.py tests/evaluation/test_eval_optimize_loop_example.py
git commit -m "feat: complete optimization candidate audit"
```

---

### Task 5: Turn the JSON Schema into an Enforced Report Contract

**Files:**
- Modify: `examples/optimization/eval_optimize_loop/optimization_report.schema.json`
- Modify: `tests/evaluation/test_eval_optimize_loop_example.py:201`

**Interfaces:**
- Consumes: fake, trace, and online report dictionaries.
- Produces: rejection of structurally incomplete or numerically invalid reports before any report file is written.

- [ ] **Step 1: Add mutation tests for every currently permissive core object**

```python
@pytest.mark.parametrize(
    ("mutation_path", "replacement"),
    [
        (("candidates", 0, "delta"), {}),
        (("baseline", "validation", "case_results"), [{}]),
        (("candidates", 0, "case_deltas"), [{}]),
        (("failure_attribution", "coverage"), 9.0),
        (("candidates", 0, "audit"), {}),
    ],
)
def test_report_schema_rejects_incomplete_core_objects(
    mutation_path: tuple[Any, ...],
    replacement: Any,
):
    module = load_pipeline_module()
    report = load_report(
        EXAMPLE_DIR / "fixtures" / "optimization_report.sample.json"
    )
    target: Any = report
    for key in mutation_path[:-1]:
        target = target[key]
    target[mutation_path[-1]] = replacement
    with pytest.raises(ValidationError):
        module.validate_report_schema(report)
```

- [ ] **Step 2: Verify all five mutations are currently accepted**

Run:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py::test_report_schema_rejects_incomplete_core_objects -q
```

Expected: FAIL because the current schema accepts the malformed replacements.

- [ ] **Step 3: Define strict case and delta objects**

Add these definitions and reference them from evaluation summaries and candidates:

```json
"keyTrace": {
  "type": "object",
  "additionalProperties": false,
  "required": [
    "invocation_id", "actual_final_response",
    "expected_final_response", "error_message"
  ],
  "properties": {
    "invocation_id": {"type": "string"},
    "actual_final_response": {"type": "string"},
    "expected_final_response": {"type": "string"},
    "error_message": {"type": ["string", "null"]}
  }
},
"evaluationCase": {
  "type": "object",
  "additionalProperties": false,
  "required": [
    "case_id", "tags", "user", "score", "passed", "metrics",
    "actual_text", "expected_text", "key_trace", "root_cause", "reasons"
  ],
  "properties": {
    "case_id": {"type": "string", "minLength": 1},
    "tags": {"type": "array", "items": {"type": "string"}},
    "user": {"type": "string"},
    "score": {"type": "number", "minimum": 0, "maximum": 1},
    "passed": {"type": "boolean"},
    "metrics": {
      "type": "object",
      "minProperties": 1,
      "additionalProperties": {"$ref": "#/$defs/metricSummary"}
    },
    "actual_text": {"type": "string"},
    "expected_text": {"type": "string"},
    "key_trace": {"$ref": "#/$defs/keyTrace"},
    "root_cause": {
      "enum": [
        "", "final_response_mismatch", "tool_call_error", "parameter_error",
        "rubric_failed", "knowledge_gap", "format_error", "runtime_error",
        "metric_failed"
      ]
    },
    "reasons": {"type": "array", "items": {"type": "string", "minLength": 1}}
  }
},
"caseDelta": {
  "type": "object",
  "additionalProperties": false,
  "required": [
    "case_id", "baseline_score", "candidate_score", "baseline_passed",
    "candidate_passed", "delta", "change_type", "baseline_actual_text",
    "candidate_actual_text", "root_cause", "reasons"
  ],
  "properties": {
    "case_id": {"type": "string", "minLength": 1},
    "baseline_score": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
    "candidate_score": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
    "baseline_passed": {"type": ["boolean", "null"]},
    "candidate_passed": {"type": ["boolean", "null"]},
    "delta": {"type": ["number", "null"], "minimum": -1, "maximum": 1},
    "change_type": {
      "enum": [
        "new_pass", "new_fail", "score_improved", "score_regressed",
        "unchanged", "missing_candidate", "unexpected_candidate"
      ]
    },
    "baseline_actual_text": {"type": "string"},
    "candidate_actual_text": {"type": "string"},
    "root_cause": {"type": "string"},
    "reasons": {"type": "array", "items": {"type": "string"}}
  }
}
```

Require `case_results` with `minItems: 1` in every evaluation summary and `case_deltas` with `minItems: 1` in every candidate.

- [ ] **Step 4: Tighten numeric, gate, attribution, and audit constraints**

Require all three delta fields, bound scores/pass rates/coverage to `[0, 1]`, require non-negative taxonomy counts, and define:

```json
"candidateAudit": {
  "type": "object",
  "additionalProperties": false,
  "required": ["seed", "duration_seconds", "cost", "config_path", "config_sha256"],
  "properties": {
    "seed": {"type": "integer"},
    "duration_seconds": {"type": "number", "minimum": 0},
    "cost": {
      "type": "object",
      "additionalProperties": false,
      "required": ["currency", "estimated", "known"],
      "properties": {
        "currency": {"const": "USD"},
        "estimated": {"type": ["number", "null"], "minimum": 0},
        "known": {"type": "boolean"}
      }
    },
    "config_path": {"type": "string", "minLength": 1},
    "config_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"}
  }
},
"optimizationRound": {
  "type": "object",
  "additionalProperties": false,
  "required": [
    "round", "optimized_field_names", "prompt_paths", "prompt_sha256",
    "validation_pass_rate", "metric_breakdown", "accepted",
    "decision_reason", "failed_case_ids", "cost_usd", "token_usage",
    "duration_seconds"
  ],
  "properties": {
    "round": {"type": "integer", "minimum": 1},
    "optimized_field_names": {
      "type": "array",
      "items": {"type": "string"}
    },
    "prompt_paths": {
      "type": "object",
      "additionalProperties": {"type": "string", "minLength": 1}
    },
    "prompt_sha256": {
      "type": "object",
      "additionalProperties": {
        "type": "string",
        "pattern": "^[a-f0-9]{64}$"
      }
    },
    "validation_pass_rate": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    },
    "metric_breakdown": {
      "type": "object",
      "additionalProperties": {"type": "number"}
    },
    "accepted": {"type": "boolean"},
    "decision_reason": {"type": "string", "minLength": 1},
    "failed_case_ids": {
      "type": "array",
      "items": {"type": "string"}
    },
    "cost_usd": {"type": "number", "minimum": 0},
    "token_usage": {
      "type": "object",
      "additionalProperties": false,
      "required": ["prompt", "completion", "total"],
      "properties": {
        "prompt": {"type": "integer", "minimum": 0},
        "completion": {"type": "integer", "minimum": 0},
        "total": {"type": "integer", "minimum": 0}
      }
    },
    "duration_seconds": {"type": "number", "minimum": 0}
  }
}
```

Require `candidate.audit`. Require `gate.validation_delta`, `new_hard_fail_ids`, `critical_regression_ids`, `missing_case_ids`, and `unexpected_case_ids`; allow `validation_delta` to be null only for malformed evidence that was rejected.

Add this top-level property and add `optimization_rounds` to the root
`required` array:

```json
"optimization_rounds": {
  "type": "array",
  "items": {"$ref": "#/$defs/optimizationRound"}
}
```

Fake and trace reports use an empty array.

- [ ] **Step 5: Validate all three modes against the schema**

Run:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "schema or fake_mode or trace_mode or online_mode_can_construct" -q
```

Expected: PASS, and every malformed mutation raises `jsonschema.ValidationError`.

- [ ] **Step 6: Commit the report contract**

```powershell
git add examples/optimization/eval_optimize_loop/optimization_report.schema.json tests/evaluation/test_eval_optimize_loop_example.py
git commit -m "feat: enforce optimization report schema"
```

---

### Task 6: Make the Public Fixtures Prove Every Required Scenario

**Files:**
- Modify: `examples/optimization/eval_optimize_loop/fixtures/fake_outputs.json`
- Modify: `examples/optimization/eval_optimize_loop/README.md`
- Modify: `tests/evaluation/test_eval_optimize_loop_example.py:171`
- Regenerate: `examples/optimization/eval_optimize_loop/fixtures/optimization_report.sample.json`

**Interfaces:**
- Produces: one accepted improvement, one rejected no-op, and one rejected aggregate validation regression with train improvement.
- Produces: a 300-500-character design explanation.

- [ ] **Step 1: Strengthen the public overfit assertion before changing fixtures**

```python
def test_public_candidates_cover_success_noop_and_aggregate_regression(tmp_path: Path):
    module = load_pipeline_module()
    report = module.make_report(
        mode="fake",
        run_id="public_scenarios",
        run_dir=tmp_path,
        seed=7,
        started=module.time.perf_counter(),
    )
    candidates = {item["id"]: item for item in report["candidates"]}
    assert candidates["candidate_local_patch"]["gate"]["accepted"] is True
    assert candidates["candidate_noop"]["delta"]["validation_score"] == 0
    assert candidates["candidate_noop"]["gate"]["accepted"] is False
    overfit = candidates["candidate_overfit"]
    assert overfit["delta"]["train_score"] > 0
    assert overfit["delta"]["validation_score"] < 0
    assert overfit["gate"]["accepted"] is False
```

- [ ] **Step 2: Verify the current overfit fixture has only net-zero validation change**

Run:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py::test_public_candidates_cover_success_noop_and_aggregate_regression -q
```

Expected: FAIL because the current overfit candidate fixes one validation case and breaks one, producing a zero aggregate delta.

- [ ] **Step 3: Make the overfit fixture regress aggregate validation**

In `candidate_overfit.outputs`, keep all three train outputs correct, keep `val_address_change_102` correct, keep `val_shipping_delay_103` incorrectly escalated, and set `val_refund_window_101` to the baseline FAQ output:

```json
"val_refund_window_101": "{\"route\":\"faq\",\"tool\":{\"name\":\"none\",\"arguments\":{}},\"reason\":\"Refund window policy question can be answered by FAQ.\"}"
```

This changes validation from baseline `2/3` to overfit `1/3` while train rises from `1/3` to `3/3`.

- [ ] **Step 4: Replace Design Notes with a 300-500-character explanation**

Use this text:

```markdown
## Design Notes

本示例把评测、归因、候选生成、验证回归和产品 gate 组织成一个可复现闭环。fake 与 trace 模式只替换 agent 输出来源，分数、逐 case pass/fail 和 metric 明细仍由 AgentEvaluator 生成，因此 CI 不依赖 API，也不会用 fixture 直接冒充分数。online 模式调用 AgentOptimizer 和 TargetPrompt，optimizer_dev 只服务优化器，val 仅参与 baseline 与最终候选复评，避免验证集答案进入 prompt 搜索。

报告先写 JSON，再渲染 Markdown。每个候选保存 prompt 摘要与哈希、训练和验证结果、逐 case 变化、失败原因、gate 检查、成本和耗时。gate 要求验证分数严格超过阈值，且不得新增 hard fail、关键 case 退化、必需 metric 失败或预算越界；成本未知且配置了成本上限时按失败处理。候选只写入运行目录，原始 prompt 不会被覆盖，随机种子、配置哈希和环境快照用于复现实验。
```

Add:

```python
def test_design_notes_length_is_within_issue_limit():
    readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Design Notes", 1)[1].split("## Verification", 1)[0]
    non_whitespace = len("".join(section.split()))
    assert 300 <= non_whitespace <= 500
```

- [ ] **Step 5: Regenerate the sample from the hardened fake pipeline**

Run:

```powershell
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode fake --output-dir runs --run-id sample
Copy-Item -LiteralPath runs/sample/optimization_report.json -Destination examples/optimization/eval_optimize_loop/fixtures/optimization_report.sample.json -Force
```

Normalize the sample deterministically:

```powershell
$code = @'
import json
import os
from pathlib import Path

repo = Path.cwd().resolve()
sample = Path("examples/optimization/eval_optimize_loop/fixtures/optimization_report.sample.json")
report = json.loads(sample.read_text(encoding="utf-8"))

def normalize(value):
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, str):
        prefix = str(repo) + os.sep
        return value.replace(prefix, "").replace("\\", "/")
    return value

report = normalize(report)
report["duration_seconds"] = 0.0
report["environment_snapshot"].update({
    "git_commit": "sample",
    "git_dirty": False,
    "python_version": "3.x",
    "sdk_version": "sample",
    "command": (
        "python examples/optimization/eval_optimize_loop/run_pipeline.py "
        "--mode fake --output-dir runs --run-id sample"
    ),
})
sample.write_text(
    json.dumps(report, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
'@
python -c $code
```

Do not remove required fields or hand-edit computed scores.

- [ ] **Step 6: Verify public scenarios, sample schema, and whitespace**

Run:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "public_candidates or design_notes or sample_report" -q
git diff --check
```

Expected: PASS; the sample has exactly one newline at EOF and no trailing blank line.

- [ ] **Step 7: Commit fixtures and documentation**

```powershell
git add examples/optimization/eval_optimize_loop/fixtures/fake_outputs.json examples/optimization/eval_optimize_loop/fixtures/optimization_report.sample.json examples/optimization/eval_optimize_loop/README.md tests/evaluation/test_eval_optimize_loop_example.py
git commit -m "docs: align optimization example with issue scenarios"
```

---

### Task 7: Restore Online Resource and Warning Observability

**Files:**
- Modify: `examples/optimization/eval_optimize_loop/run_pipeline.py:188`
- Modify: `examples/optimization/eval_optimize_loop/run_pipeline.py:1469`
- Modify: `trpc_agent_sdk/models/openai_adapter/_deepseek.py:53`
- Modify: `tests/evaluation/test_eval_optimize_loop_example.py:971`

**Interfaces:**
- Guarantees: every per-call `Runner` is closed in a `finally` block.
- Guarantees: provider limitations remain visible and are not hidden by repository-wide log-level changes.

- [ ] **Step 1: Add a unit test that Runner closes on success and failure**

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("raise_during_run", [False, True])
async def test_online_call_agent_closes_runner(
    monkeypatch: pytest.MonkeyPatch,
    raise_during_run: bool,
):
    module = load_pipeline_module()
    closed = []

    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        async def run_async(self, **kwargs):
            if raise_during_run:
                raise RuntimeError("model stream failed")
            if False:
                yield None

        async def close(self):
            closed.append(True)

    import trpc_agent_sdk.runners as runners

    monkeypatch.setattr(runners, "Runner", FakeRunner)
    monkeypatch.setattr(
        module,
        "_make_llm_agent_from_prompts",
        lambda prompt_texts: object(),
    )
    call_agent = module.make_online_call_agent(
        system_prompt=EXAMPLE_DIR / "agent" / "prompts" / "system.md",
        router_prompt=EXAMPLE_DIR / "agent" / "prompts" / "router.md",
    )
    if raise_during_run:
        with pytest.raises(RuntimeError, match="model stream failed"):
            await call_agent("hello")
    else:
        assert await call_agent("hello") == ""
    assert closed == [True]
```

- [ ] **Step 2: Verify the current implementation leaks the Runner lifecycle**

Run:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py::test_online_call_agent_closes_runner -q
```

Expected: FAIL because `runner.close()` is never called.

- [ ] **Step 3: Close Runner in a finally block**

Wrap the run loop in:

```python
        try:
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=Content(role="user", parts=[Part.from_text(text=query)]),
            ):
                if not event.is_final_response() or not event.content:
                    continue
                for part in event.content.parts or []:
                    if not part.thought and part.text:
                        final += part.text
        finally:
            await runner.close()
        return final.strip()
```

- [ ] **Step 4: Remove symptom-hiding changes**

Delete `KNOWN_ONLINE_WARNING_FILTERS`, `install_known_online_warning_filters`, and its call from `run_online`. Restore:

```python
logger.warning(
    "DeepSeek only supports JSON object response_format; response schema is ignored."
)
```

Remove the real-API assertions that require those warning strings to disappear. Replace them with report assertions for successful completion, gate correctness, source-prompt immutability, schema validity, and no secret values.

- [ ] **Step 5: Re-run online wiring tests before consuming API**

Run:

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -k "online and not e2e" -q
```

Expected: PASS with no real API call.

- [ ] **Step 6: Run both real-API product decisions explicitly**

Accepted path:

```powershell
$env:RUN_ONLINE_E2E='1'
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py::test_online_e2e_smoke_with_real_api -q -s
```

Rejected path:

```powershell
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode online --output-dir runs/online_verify --run-id default_reject --gate-config runs/judge_20260710/online_gate_300.json
```

Expected accepted path: baseline validation below candidate validation and `gate_decision.accepted == true`.

Expected rejected path: baseline validation equals candidate validation and the gate reason contains `validation score did not improve`.

- [ ] **Step 7: Commit online lifecycle and observability**

```powershell
git add examples/optimization/eval_optimize_loop/run_pipeline.py trpc_agent_sdk/models/openai_adapter/_deepseek.py tests/evaluation/test_eval_optimize_loop_example.py
git commit -m "fix: close online evaluation resources"
```

---

### Task 8: Final Verification, Scope Audit, and Honest Test Claim

**Files:**
- Review: `examples/optimization/eval_optimize_loop/**`
- Review: `tests/evaluation/test_eval_optimize_loop_example.py`
- Review: `trpc_agent_sdk/evaluation/_agent_evaluator.py`
- Review: `pyproject.toml`
- Review separately: `tests/conftest.py`

**Interfaces:**
- Produces: a clean, issue-scoped commit series and an evidence-backed completion statement.

- [ ] **Step 1: Confirm no final-validation leakage**

Run:

```powershell
rg -n "val_path|val_evalset|validation_dataset_path|best_prompts|optimize\\(" examples/optimization/eval_optimize_loop/run_pipeline.py
```

Verify manually:

- `AgentOptimizer.optimize(... validation_dataset_path=optimizer_dev_path)`;
- `val_path` is absent from optimizer inputs and reflection context;
- `val_path` is used only in baseline final scoring, candidate final scoring, deltas, and gate decisions.

- [ ] **Step 2: Run deterministic acceptance tests**

```powershell
python -m pytest tests/evaluation/test_eval_optimize_loop_example.py -q
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode fake --output-dir runs/final_verify --run-id fake
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode trace --output-dir runs/final_verify --run-id trace
```

Expected: focused suite passes with only the explicit online opt-in skip; fake and trace finish below 180 seconds and write schema-valid JSON plus Markdown.

- [ ] **Step 3: Run evaluation regressions**

```powershell
python -m pytest tests/evaluation -q
```

Expected: exit 0, excluding only explicitly reported opt-in skips.

- [ ] **Step 4: Run style and source checks in the declared dev environment**

```powershell
python -m pip install -e ".[dev,eval,optimize]"
python -m black --check examples/optimization/eval_optimize_loop tests/evaluation/test_eval_optimize_loop_example.py tests/conftest.py
python -m flake8 examples/optimization/eval_optimize_loop tests/evaluation/test_eval_optimize_loop_example.py tests/conftest.py
python -m compileall -q examples/optimization/eval_optimize_loop trpc_agent_sdk/evaluation/_agent_evaluator.py
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 5: Treat full-repository failures as a separate compatibility decision**

Run:

```powershell
python -m pytest -q
```

If Windows still fails POSIX-specific suites, record the exact failures and verify the same commit in the repository's supported Linux CI image. Do not broaden this issue into code-executor, shell-tool, symlink, file-mode, or LangGraph-version fixes. Do not use `tests/conftest.py` to hide platform failures; either remove that file from this issue's diff or move its optional-dependency policy into a separately reviewed test-infrastructure change.

- [ ] **Step 6: Audit the final diff for scope and secrets**

```powershell
git diff origin/main...HEAD --stat
git diff origin/main...HEAD -- examples/optimization/eval_optimize_loop tests/evaluation/test_eval_optimize_loop_example.py trpc_agent_sdk/evaluation/_agent_evaluator.py pyproject.toml
$changed = git diff --unified=0 origin/main...HEAD
$hits = $changed | Select-String -Pattern "(sk-[A-Za-z0-9_-]{12,}|TRPC_AGENT_API_KEY=.+|Authorization: Bearer .+)"
if ($hits) { $hits; throw "possible credential found in changed lines" }
```

Expected: no credentials, no generated `runs/` content, no source-prompt mutation, and no unrelated production refactor.

- [ ] **Step 7: Create the final verification commit only if needed**

If verification changed only sample normalization or documentation:

```powershell
git add examples/optimization/eval_optimize_loop/fixtures/optimization_report.sample.json examples/optimization/eval_optimize_loop/README.md
git commit -m "chore: finalize optimization example artifacts"
```

If verification changed nothing, do not create an empty commit.

---

## Issue Coverage Map

| Issue requirement | Plan coverage |
| --- | --- |
| Train and validation baseline scoring through AgentEvaluator | Tasks 3, 8 |
| Per-case metric, pass/fail, reason, and key trace | Tasks 1, 3, 5 |
| Failure clustering with explainable reasons | Tasks 1, 5 |
| AgentOptimizer/TargetPrompt optimization | Tasks 4, 7, 8 |
| Final-validation re-run and per-case change classes | Tasks 2, 3 |
| Configurable score, hard-fail, critical, cost, duration, metric gates | Tasks 2, 5 |
| Every candidate/round prompt, result, decision, cost, duration, seed | Task 4 |
| JSON and Markdown reports | Tasks 3, 5, 6 |
| Fake/trace without API and below three minutes | Tasks 6, 8 |
| Three train plus three validation public cases | Task 6 |
| Success, no-op, and aggregate regression scenarios | Task 6 |
| Hidden decision robustness at or above 80% | Tasks 1, 2, 5 |
| Failure attribution robustness at or above 75% | Tasks 1, 5 |
| 300-500-character design explanation | Task 6 |
| Real API accepted and rejected paths | Task 7 |
| Honest repository-wide test statement | Task 8 |

## Completion Standard

The work is complete only when the focused suite, evaluation suite, fake run, trace run, report-schema mutation tests, adversarial gate matrix, adversarial attribution matrix, and both opt-in real-API decisions have fresh passing evidence. A Linux full-suite result may be reported separately from Windows platform failures, but neither may be described as passing without an exit-zero command from that environment.
