# Eval-Optimize Loop — Automated Evaluate · Attribute · Optimize · Regress · Audit

[中文版](README.md) ｜ Design note: [DESIGN.md](DESIGN.md)

> **Runs with zero API keys**: `python run_pipeline.py --scenario all` — all three
> scenarios finish end-to-end in under a minute.

## 1 · Problem & Design Goals

`AgentOptimizer` can produce prompts with higher scores, but "higher score" does not
mean "safe to ship":

- the optimizer may only see **weak metrics** (black-box mode has no tool trajectory /
  knowledge-recall signal);
- if the optimizer's tuning set leaks from the training distribution it will
  **overfit** without noticing;
- without per-case comparison you cannot tell whether the gain was paid for by
  breaking previously-passing key cases;
- without audit artifacts, an improved prompt still cannot pass a production review.

This example wires `AgentEvaluator` and `AgentOptimizer` into a **reproducible
six-stage closed loop** that answers one question: **is this candidate prompt worth
accepting?**

```
① baseline eval            ② failure attribution    ③ optimization
train+val × 4 metrics  →   clustering into 6     →  AgentOptimizer (GEPA)
(per-case score/traj)      failure types            (2 TargetPrompt fields)
                                                          │
⑥ audit artifacts      ⑤ acceptance gates        ④ candidate validation
report json+md     ←   6 configurable gates  ←   independent re-eval
per-round cand/cost    (all must pass)           per-case delta
```

### The minimal demo

A "City Info Assistant": distance conversion (`convert_distance` tool), city
introductions (`knowledge_search` tool + source citation), identity questions (no
tool). The baseline prompt has three defects (unnormalized unit, no knowledge
retrieval, non-JSON output); 6 committed cases (3 train + 3 val) expose all of them.
Three built-in scenarios cover the three canonical outcomes:

| Scenario | Candidate proposed by the optimizer | Independent re-eval | Gate decision |
| --- | --- | --- | --- |
| `success` | fixes all three defects | train 1/3→3/3, val 1/3→3/3 | ✅ ACCEPT |
| `no_effect` | copy-editing only (directives unchanged) | all unchanged | ❌ REJECT (insufficient improvement) |
| `overfit` | memorizes training samples | train 1/3→3/3, **val 1/3→0/3** | ❌ REJECT (overfit guard) |

## 2 · Terminology

| Term | Meaning |
| --- | --- |
| Acceptance suite | `data/eval_config.json`: trajectory + exact response + rubric + knowledge recall (4 metrics), used for regression eval |
| Optimizer's weak metric | `optimizer.json` has only `final_response_avg_score`: the SDK forbids trajectory/recall metrics in black-box `call_agent` mode — this information gap is part of the overfitting story |
| Leaked tuning set | `data/optimizer_probe.evalset.json`: same distribution as train; fed to the optimizer as its "validation set" only in the overfit scenario |
| Protected cases | `protected_cases` in `pipeline.json`: whitelist of key cases; any regression rejects the candidate |
| Directive DSL | the `<!-- directives: ... -->` comment block in prompts; the fake agent parses it, so prompt edits change behavior offline for real |
| Trace mode | `evalMode: "trace"` in the evalset: evaluate/attribute pre-recorded traces without running the agent |

## 3 · Running

### 3.1 Zero-dependency run (no env vars / API keys)

```bash
# default scenario: success
python examples/optimization/eval_optimize_loop/run_pipeline.py

# all three scenarios (recommended first run)
python examples/optimization/eval_optimize_loop/run_pipeline.py --scenario all

# additionally evaluate/attribute the pre-recorded baseline traces (trace mode)
python examples/optimization/eval_optimize_loop/run_pipeline.py --baseline-from-trace

# write the best candidate back to loop_agent/prompts/ when gates pass (mutates sources!
# with --scenario all the write is deferred until every scenario has finished)
python examples/optimization/eval_optimize_loop/run_pipeline.py --apply

# validate a report against the schema contract
python examples/optimization/eval_optimize_loop/run_pipeline.py --check sample_output/success/optimization_report.json
```

Tests:

```bash
python -m pytest examples/optimization/eval_optimize_loop/tests -q
```

### 3.2 Output layout

```
runs/<scenario>-<timestamp>/
├── optimization_report.json       # structured report: baseline / candidate / delta / attribution / gate decision
├── optimization_report.md         # human-readable verdict with all the evidence
├── baseline_eval.json             # stage ① raw per-case records
├── candidate_eval.json            # stage ④ raw per-case records
├── attribution.json               # stage ② findings
├── pipeline_config.snapshot.json  # gate/seed config snapshot of this run
└── optimize/                      # stage ③ native SDK audit directory
    ├── result.json  summary.txt  run.log  config.snapshot.json
    ├── rounds/round_001.json …    # per-round candidate prompts, acceptance reason, cost, duration
    └── baseline_prompts/  best_prompts/
```

The committed `sample_output/` holds the three reports from `--scenario all`; to
regenerate:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py --scenario all --output /tmp/regen
# then copy /tmp/regen/<scenario>-*/optimization_report.{json,md} into sample_output/<scenario>/
```

## 4 · Inputs / Outputs

| File | Role |
| --- | --- |
| `data/train.evalset.json` | 3 training cases (reflection minibatch source) |
| `data/val.evalset.json` | 3 independent validation cases (final referee for regression) |
| `data/optimizer_probe.evalset.json` | 3 leaked tuning cases (fed to the optimizer only in `overfit`) |
| `data/trace_baseline.evalset.json` | 2 trace-mode cases (pre-recorded baseline failures) |
| `data/eval_config.json` | acceptance metric suite (4 metrics, fake judge) |
| `optimizer.json` / `configs/optimizer.*.json` | optimizer configs (scenarios differ only in `reflection_lm.model_name`) |
| `pipeline.json` | gate thresholds / protected cases / budgets / seed |
| `loop_agent/prompts/system.md`, `skill.md` | the two TargetPrompt source files |
| `candidates/*.md` | the fake reflection LM's proposal library (scenario × field) |
| `sample_output/*/optimization_report.{json,md}` | sample reports for the three scenarios |

## 5 · Gate rules (stage ⑤)

All six gates must pass; the rejection reason cites the most severe failed gate
(overfit > protected case > new hard fail > insufficient improvement > budgets).
Every threshold is configurable in `pipeline.json`.

| Gate | Rule | Config |
| --- | --- | --- |
| `min_val_improvement` | val pass-rate gain ≥ threshold AND mean-score gain ≥ threshold | `min_val_pass_rate_improvement` / `min_val_score_improvement` |
| `no_new_hard_fail` | no case may flip pass→fail | `forbid_new_hard_fail` |
| `protected_cases` | any new_fail / score_down on a protected case rejects | `protected_cases` |
| `overfit_guard` | train pass-rate ↑ AND val pass-rate ↓ ⇒ overfitting | `overfit_guard` |
| `cost_budget` | optimization cost ≤ budget; metric calls ≤ budget (optional) | `max_cost_usd` / `max_metric_calls` |
| `duration_budget` | pipeline wall-clock ≤ budget | `max_duration_seconds` |

The 12-row decision matrix lives in `tests/test_gates.py::DECISION_MATRIX` (12/12 pass).

## 6 · Failure-attribution rules (stage ②)

The rules depend only on the structure of framework metric results, not on this
example's cases (they generalize to hidden samples); every failing case is
**guaranteed at least one** evidence-backed reason (a per-metric fallback mapping
covers anything the rules miss).

| Failure type | Trigger |
| --- | --- |
| `wrong_tool_call` | trajectory metric failed and the actual/expected call **name multisets** differ (missing/extra/wrong tool) |
| `wrong_tool_args` | trajectory metric failed with equal names but different arguments |
| `knowledge_recall_miss` | recall rubric failed; or the missing call is a knowledge tool (reported alongside the trajectory finding) |
| `format_violation` | response metric failed and the expected text parses as JSON while the actual does not |
| `llm_rubric_fail` | response-quality rubric failed (evidence = failing rubric ids + reasons) |
| `final_answer_mismatch` | any other response-metric failure |

Primary (root-cause) precedence: `wrong_tool_call` > `wrong_tool_args` >
`knowledge_recall_miss` > `format_violation` > `llm_rubric_fail` >
`final_answer_mismatch` (trajectory errors sit upstream in the chain).

## 7 · Design notes

### 7.1 Optimizer metrics ≠ acceptance metrics (a deliberate information gap)

Black-box `call_agent` mode cannot capture tool trajectories or tool responses, and
the SDK **hard-rejects** `tool_trajectory_avg_score` / `llm_rubric_knowledge_recall`
in that mode. So `optimizer.json` carries only exact response matching — exactly the
situation in real businesses where the optimizer sees a weaker signal than the
acceptance suite. That is why the loop exists: **the optimizer's claim of improvement
only counts after re-evaluation with the full suite on an independent validation
set.** The overfit scenario pushes this gap to the extreme (optimizer view 0/3→3/3,
independent re-eval val 1/3→0/3).

### 7.2 How the three fake models run with zero API keys (no SDK changes)

Judge and reflection-LM configs both support `provider_name`; any non-openai provider
routes through `ModelRegistry.create_model("{provider}/{model}")` regex matching. The
example registers three deterministic fake providers:

- **fake agent** parses the directive DSL in the prompt and changes behavior
  accordingly — prompt edits produce real behavior/score differences offline;
- **fake judge** evaluates rubric text via backtick tokens; its conditional rule
  ("如果…" + condition-not-applicable ⇒ yes) mirrors the real judge prompt, and its
  JSON output is isomorphic to the real judge's, parsed by the SDK's own scorer;
- **fake reflection** finds the `<!-- prompt-field: X -->` marker in the reflection
  request and returns `candidates/X.<scenario>.md`, the scenario taken from its own
  `model_name`.

### 7.3 Why the packages are named `loop_agent` / `loop_pipeline`

pytest may import several examples in one process: `agent` is used by most examples
and `pipeline` is already taken by `multi_agent_pipeline`; duplicate top-level names
clobber each other in `sys.modules`.

### 7.4 Semantics of `--apply`

The optimizer itself always runs with `update_source=False` (sources are restored
when it finishes). Writing the best candidate back to `loop_agent/prompts/` requires
**both** an accepting gate decision **and** the explicit `--apply` flag, so a
rejected candidate can never reach the source files, and even accepted ones land in
the audit directory by default.

## 8 · Adapting to your own business

1. **Swap the agent**: replace `loop_agent/`, keeping the two entry points —
   `get_agent_async()` (evaluator agent_module mode, captures tool trajectories) and
   `call_agent(query) -> str` (optimizer black-box callback). Both must re-read the
   prompt sources on every call.
2. **Swap the data**: point `data/train.evalset.json` / `data/val.evalset.json` at
   your business cases. **The validation set must be independent of training** (the
   SDK guards against same-file leakage; same-distribution-different-file leakage is
   what this loop's overfit guard is for).
3. **Swap the acceptance suite**: configure a real `judge_model` in
   `data/eval_config.json` (drop `provider_name: fake-judge`, set
   `model_name`/`api_key`/`base_url`).
4. **Swap the optimizer config**: same for `reflection_lm` in `optimizer.json`;
   black-box mode allows response-based metrics only.
5. **Tune the gates**: adjust `pipeline.json` to your risk profile — add key
   regression cases to `protected_cases`, set `max_cost_usd` from real model pricing.

## 9 · FAQ

**Q: Why does the `no_effect` scenario report `SUCCEEDED` yet get rejected?**
`OptimizeResult.status=SUCCEEDED` only means the loop terminated normally
(`finish_reason=no_improvement`). Acceptance is the pipeline gates' job — the two
verdict layers are deliberately separate.

**Q: Why does the overfit guard require "train↑ AND val↓" instead of just val↓?**
A val drop alone only says the candidate is bad; a simultaneous train rise is the
fingerprint of overfitting, letting the report give an actionable diagnosis ("your
tuning set leaks from training") instead of a generic "it got worse".

**Q: When is trace mode useful?**
When you already have production trace logs and want attribution before deciding to
run optimization: `--baseline-from-trace` evaluates and attributes
`data/trace_baseline.evalset.json` (`evalMode: "trace"`) with zero agent execution.

**Q: Why is the reported cost 0?**
Fake models incur no token cost. With real models, `OptimizeResult.total_llm_cost` /
`total_token_usage` flow into the report and the `cost_budget` gate automatically.
