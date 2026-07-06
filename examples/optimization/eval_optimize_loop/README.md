# Evaluation + Optimization Loop

This example demonstrates a reproducible pipeline that connects evaluation,
failure attribution, prompt optimization, validation regression checks, gate
decisions, and audit reports.

It is intentionally different from `ci_integration/`: CI integration focuses on
PR gates and nightly write-back, while this example focuses on the complete
review loop before deciding whether a candidate prompt is worth accepting.

## Run

The default mode is deterministic and does not require an API key:

```bash
cd examples/optimization/eval_optimize_loop
PYTHONPATH=../../.. python run_pipeline.py --mode fake
```

If your environment only exposes `python3`, use:

```bash
PYTHONPATH=../../.. python3 run_pipeline.py --mode fake
```

To refresh the committed sample reports:

```bash
PYTHONPATH=../../.. python run_pipeline.py --mode fake --update-sample-outputs
```

The fake and trace modes support three deterministic scenarios:

```bash
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario overfit
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario accepted
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario cost_exceeded
```

- `overfit` is the default: train improves, validation does not improve, and a
  critical case regresses, so the gate rejects the candidate.
- `accepted`: train and validation both improve with no new hard fail, so the
  gate accepts the candidate.
- `cost_exceeded`: quality improves, but the estimated cost exceeds
  `gate.max_cost_usd`, so the gate rejects the candidate.

For CI-style scripts, add `--ci-exit-code`. Normal demo runs always exit 0 after
writing the report; CI mode exits 0 for accepted candidates and 1 for rejected
candidates:

```bash
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario accepted --ci-exit-code
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario overfit --ci-exit-code
```

Trace replay mode also runs without model credentials. It first records fake
outputs into generated `eval_mode: "trace"` evalsets, then evaluates those
`actual_conversation` records without invoking `call_agent`:

```bash
PYTHONPATH=../../.. python run_pipeline.py --mode trace
```

The optional real optimizer path delegates candidate search to
`AgentOptimizer.optimize` and requires the normal optimization dependencies and
model environment variables:

```bash
export TRPC_AGENT_API_KEY="<your-key>"
export TRPC_AGENT_BASE_URL="<your-endpoint>"
export TRPC_AGENT_MODEL_NAME="<your-model>"
PYTHONPATH=../../.. python run_pipeline.py --mode optimizer
```

## Verification Commands

Use these commands to exercise the important review paths:

```bash
cd examples/optimization/eval_optimize_loop
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario overfit
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario accepted
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario cost_exceeded
PYTHONPATH=../../.. python run_pipeline.py --mode trace --scenario overfit
cd ../../..
pytest tests/evaluation/test_eval_optimize_loop_example.py -q
```

## Inputs

| File | Role |
| --- | --- |
| `train.evalset.json` | Training split used for baseline scoring and optimization feedback. |
| `val.evalset.json` | Validation split used for regression and gate decisions. |
| `optimizer.json` | Shared metric, optimizer, fake candidate, and gate configuration. |
| `case_meta.json` | Public-case attribution labels used for local self-checking. |
| `agent/prompts/router.md` | Router prompt source registered as `TargetPrompt("router_prompt")`. |
| `agent/prompts/system.md` | System prompt source registered as `TargetPrompt("system_prompt")`. |
| `agent/prompts/skill.md` | Skill prompt source registered as `TargetPrompt("skill_prompt")`. |

The fake mode still uses `AgentEvaluator` for all train and validation scoring.
Only candidate generation is deterministic, so the example can run in CI or on a
laptop without model credentials.

At startup, the pipeline validates that all required input files exist, train and
validation evalsets are distinct and non-empty, gate keys are present, critical
case IDs refer to validation cases, and evaluator parallelism is positive.

Fake judge behavior is implemented by local exact-match final-response scoring
(`final_response_avg_score`). The report records this as
`run.judge_mode = "local_exact_match_fake_judge"`, so the evaluation path does
not need an LLM judge or API key.

For tool-only and rubric-only failure classes, fake judge mode uses
deterministic attribution hints from `case_meta.json`. This lets the no-key
example exercise the full attribution taxonomy without requiring real tool
execution or an LLM rubric judge.

## Outputs

Each run writes to `runs/<mode>_<scenario>_<timestamp>/` by default:

| File or directory | Contents |
| --- | --- |
| `optimization_report.json` | Machine-readable baseline, candidate, per-round prompts, delta, attribution, gate, and audit payload. |
| `optimization_report.md` | Human-readable report for review. |
| `candidate_prompts/` | Candidate prompt text per target field. |
| `eval_*` | Raw `AgentEvaluator` result files for every phase. |
| `config.snapshot.json` | Reproducible copy of the input config. |
| `eval_metrics.snapshot.json` | Evaluator-compatible metric config extracted from `optimizer.json.evaluate`. |

The report also includes `prompt_audit`, which records each target prompt's
source path, baseline SHA-256, candidate SHA-256, character counts, and whether
the candidate changed the prompt. Each prompt audit entry also includes a capped
unified diff preview, so reviewers can see what changed without opening the
candidate prompt files. This makes the score-to-prompt relationship auditable
even when reports are copied outside the run directory.

`input_audit` records stable SHA-256 hashes and byte counts for the train
evalset, validation evalset, optimizer config, case metadata, and prompt source
files. This makes a report reproducible even after it is detached from the
working tree.

`audit.cost` splits the total estimated spend into optimizer and evaluation
components, then applies the same total to the configurable budget gate. In
fake/trace mode both components are deterministic and normally zero except for
the dedicated `cost_exceeded` scenario.

`failure_attribution.self_check` compares attribution results against the public
gold labels in `case_meta.json`. Gold labels live in `expected_failure_types`.
For categories that cannot naturally arise in the no-key black-box path, such as
tool-call and LLM-rubric failures, `fake_attribution_hints` provides an explicit
fake-judge hint. The report marks those cases as `hint_assisted` and also reports
rule-only accuracy separately. This is not a hidden-set proof, but it provides a
local accuracy guard for the bundled examples and fails report validation if the
labeled-case accuracy drops below 75%.

Committed examples live in `sample_outputs/`.

The JSON report includes both human reasons and structured gate checks:

```json
{
  "gate": {
    "decision": "rejected",
    "reasons": ["..."],
    "checks": [
      {"name": "min_validation_score_delta", "passed": false},
      {"name": "no_new_hard_fail", "passed": false}
    ]
  }
}
```

## Gate Decision Contract

A candidate is accepted only when every configured gate check passes:

| Check | Accept condition | Reject condition |
| --- | --- | --- |
| `min_validation_score_delta` | Validation score delta is greater than or equal to `gate.min_validation_score_delta`. | Validation score delta is below the configured threshold. |
| `no_new_hard_fail` | No validation case changes from pass to fail, unless `gate.allow_new_hard_fail` is true. | At least one validation case becomes a new hard fail while new hard fails are disallowed. |
| `critical_case_regression` | No case in `gate.critical_case_ids` loses score. | Any critical validation case regresses. |
| `overfitting_guard` | Validation improves, or training does not improve. | Training improves while validation does not improve. |
| `max_cost_usd` | Estimated candidate cost is less than or equal to `gate.max_cost_usd`. | Estimated candidate cost exceeds the budget. |

This contract is intentionally validation-first: training improvements alone
never justify acceptance, and hidden samples are expected to follow the same
structured decision rules.

## Case Coverage

The ten sample cases exceed the minimum requirement and cover the required outcomes:

| Case | Split | Expected behavior |
| --- | --- | --- |
| `train_refund_double_charge` | train | Candidate fixes a baseline failure. |
| `train_password_reset` | train | Already passing; candidate is unchanged. |
| `train_legacy_sync` | train | Candidate does not help. |
| `train_plan_question` | train | Stable non-target billing question remains passing. |
| `train_policy_tool_missing` | train | Tool lookup failure attribution with an incomplete JSON response. |
| `val_vip_refund` | validation | Candidate creates a new pass. |
| `val_plan_question` | validation | Candidate has no effect. |
| `val_checkout_outage` | validation | Candidate regresses a critical case. |
| `val_mobile_crash` | validation | Stable technical troubleshooting case remains passing. |
| `val_rubric_tone_fail` | validation | Deterministic rubric failure attribution remains failing. |

The default fake candidate improves train score but does not improve validation
overall because it also introduces a new hard fail. The gate therefore rejects
the candidate and records the overfitting reason.

## Requirement Mapping

| #91 requirement | Implementation |
| --- | --- |
| Baseline train/validation evaluation | `run_pipeline.py::evaluate_dataset` uses `AgentEvaluator.get_executer(...).get_result()`. |
| Per-case metric, pass/fail, reason, trace | Report cases include query, expected, actual, metric details, status, failure types, and reason. |
| Failure attribution clustering | `attribute_failure` and `build_failure_stats`. |
| Full attribution taxonomy | Public sample failures include `final_response_mismatch`, `tool_call_error`, `parameter_error`, `llm_rubric_not_met`, `knowledge_recall_insufficient`, and `format_violation`. |
| Attribution accuracy guard | `case_meta.json` plus `failure_attribution.self_check` validates public labeled cases and marks fake hint-assisted cases. |
| Prompt optimization target | `TargetPrompt` registers `router_prompt`, `system_prompt`, and `skill_prompt`. |
| AgentOptimizer or equivalent extension | `--mode optimizer` delegates to `AgentOptimizer`; fake/trace modes use deterministic candidate generation for no-key CI. |
| Candidate validation and per-case delta | `build_case_deltas` marks `new_pass`, `new_fail`, `score_improved`, `score_regressed`, and `unchanged`. |
| Configurable gate | `optimizer.json.gate` plus `apply_gate`, including structured `gate.checks`. |
| Overfitting rejection | Default `--scenario overfit` improves train but rejects because validation does not improve and a critical case regresses. |
| Cost budget rejection | `--scenario cost_exceeded` improves quality but fails `max_cost_usd`. |
| Audit artifacts | `input_audit`, `prompt_audit` with diff previews, `optimization_rounds`, `candidate_prompts/`, raw `eval_*` outputs, config snapshots, JSON report, and Markdown report. |
| No API key mode | `--mode fake` and `--mode trace`. |
| 6 public cases | `train.evalset.json` has 5 cases and `val.evalset.json` has 5 cases. |

## Design Notes

本示例把 `AgentEvaluator.get_executer(...).get_result()` 作为唯一评测事实来源，分别对训练集和验证集生成逐 case 记录，包含输入、期望输出、实际输出、metric 分数、pass/fail、失败类型和原因。失败归因采用可复现规则：JSON 解析失败或缺字段归为格式不符合要求，category 不一致归为知识召回不足，priority 不一致归为参数错误，action 不一致归为最终回复不匹配；对必须依赖真实工具轨迹或 LLM rubric judge 的类别，fake mode 通过 `case_meta.json` 的 `fake_attribution_hints` 标记 `tool_call_error` 和 `llm_rubric_not_met`，并在 self-check 中区分 hint-assisted 与 rule-only 准确率，避免把公开标签直接当成黑盒判断结果。

候选 prompt 不会直接写回源文件，而是先在验证集回归，并和 baseline 做逐 case delta，标记新增通过、新增失败、分数提升、分数下降和无变化。gate 由 `optimizer.json` 配置，检查验证集总分提升阈值、是否新增 hard fail、关键 case 是否退化以及成本预算；额外防过拟合规则是训练集提升但验证集没有提升时拒绝候选。这样即使优化器记住训练失败样例，只要验证集没有真实收益，或者牺牲了关键 case，也不会进入可接受状态。

审计产物包括配置快照、随机种子、候选 prompt 文件、`optimization_rounds`、原始 evaluator 输出、JSON 报告和 Markdown 报告，便于完整复现实验配置，也便于后续 CI 读取结构化字段。`prompt_audit` 记录源路径、baseline/candidate 哈希、字符数和是否变更，reviewer 可以把分数变化追溯到具体 prompt。trace mode 会生成 `eval_mode: "trace"` 数据并回放 `actual_conversation`，保证无 API key 环境也能复现 baseline、归因、优化、验证、gate 和审计闭环。
