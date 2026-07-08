# 评测 + 优化闭环示例

本示例展示一个可复现的评测优化流水线，将 baseline 评测、失败归因、prompt 候选生成、验证集回归、gate 决策和审计报告串成闭环。

它和 `ci_integration/` 的定位不同：`ci_integration/` 更关注 PR gate 和定时写回；本示例关注在接受候选 prompt 之前，如何完整完成训练集反馈、验证集回归、防过拟合判断和审计留痕。

## 运行方式

默认 fake mode 是确定性的，不需要 API key：

```bash
cd examples/optimization/eval_optimize_loop
PYTHONPATH=../../.. python run_pipeline.py --mode fake
```

如果环境只暴露 `python3`，可以运行：

```bash
PYTHONPATH=../../.. python3 run_pipeline.py --mode fake
```

刷新已提交的示例报告：

```bash
PYTHONPATH=../../.. python run_pipeline.py --mode fake --update-sample-outputs
```

fake 和 trace mode 支持三个确定性场景：

```bash
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario overfit
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario accepted
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario cost_exceeded
```

- `overfit` 是默认场景：训练集提升，验证集没有提升，并且关键 case 退化，所以 gate 拒绝候选。
- `accepted`：训练集和验证集都提升，且没有新增 hard fail，所以 gate 接受候选。
- `cost_exceeded`：质量提升，但估算成本超过 `gate.max_cost_usd`，所以 gate 拒绝候选。

CI 脚本可以添加 `--ci-exit-code`。普通 demo 运行在写出报告后总是返回 0；CI 模式下，接受候选返回 0，拒绝候选返回 1：

```bash
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario accepted --ci-exit-code
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario overfit --ci-exit-code
```

trace replay mode 也不需要模型凭证。它先用 fake 输出生成 `eval_mode: "trace"` 的 evalset，再回放其中的 `actual_conversation`，不调用 `call_agent`：

```bash
PYTHONPATH=../../.. python run_pipeline.py --mode trace
```

可选的真实优化路径会委托 `AgentOptimizer.optimize` 搜索候选，需要常规优化依赖和模型环境变量：

```bash
export TRPC_AGENT_API_KEY="<your-key>"
export TRPC_AGENT_BASE_URL="<your-endpoint>"
export TRPC_AGENT_MODEL_NAME="<your-model>"
PYTHONPATH=../../.. python run_pipeline.py --mode optimizer
```

## 验证命令

以下命令覆盖主要评审路径：

```bash
cd examples/optimization/eval_optimize_loop
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario overfit
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario accepted
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario cost_exceeded
PYTHONPATH=../../.. python run_pipeline.py --mode trace --scenario overfit
cd ../../..
pytest tests/evaluation/test_eval_optimize_loop_example.py -q
```

## 输入文件

| 文件 | 作用 |
| --- | --- |
| `train.evalset.json` | 用于 baseline 评分和优化反馈的训练集。 |
| `val.evalset.json` | 用于回归检查和 gate 决策的验证集。 |
| `optimizer.json` | 共享 metric、优化器、fake 候选和 gate 配置。 |
| `case_meta.json` | 公开 case 的归因标签，用于本地 self-check。 |
| `agent/prompts/router.md` | 注册为 `TargetPrompt("router_prompt")` 的路由 prompt 源文件。 |
| `agent/prompts/system.md` | 注册为 `TargetPrompt("system_prompt")` 的系统 prompt 源文件。 |
| `agent/prompts/skill.md` | 注册为 `TargetPrompt("skill_prompt")` 的 skill prompt 源文件。 |

fake mode 仍然使用 `AgentEvaluator` 完成训练集和验证集评分，只把候选生成做成确定性逻辑，因此可以在 CI 或没有模型凭证的本地环境运行。

启动时，流水线会校验输入文件存在、训练集和验证集不同且非空、gate key 完整、关键 case id 属于验证集、evaluator 并发数为正。

fake judge 由本地 exact-match final-response scoring 实现，报告中记录为 `run.judge_mode = "local_exact_match_fake_judge"`，因此评测路径不依赖 LLM judge 或 API key。

对于 tool-only 和 rubric-only 的失败类别，fake judge 使用 `case_meta.json` 中的确定性归因提示。这让无 key 示例也能覆盖完整失败归因 taxonomy，而不需要真实工具执行或 LLM rubric judge。

## 输出文件

每次运行默认写入 `runs/<mode>_<scenario>_<timestamp>/`：

| 文件或目录 | 内容 |
| --- | --- |
| `optimization_report.json` | 机器可读的 baseline、candidate、round prompts、delta、归因、gate 和审计数据。 |
| `optimization_report.md` | 面向 reviewer 的中英双语 Markdown 报告。 |
| `candidate_prompts/` | 每个 target field 的候选 prompt 文本。 |
| `eval_*` | 每个阶段的原始 `AgentEvaluator` 结果文件。 |
| `config.snapshot.json` | 输入配置的可复现快照。 |
| `eval_metrics.snapshot.json` | 从 `optimizer.json.evaluate` 提取的 evaluator metric 配置。 |

报告包含 `prompt_audit`，记录每个 target prompt 的源路径、baseline SHA-256、candidate SHA-256、字符数和是否变更。每条 prompt audit 还包含截断后的 unified diff preview，reviewer 不打开候选 prompt 文件也能看到变化。

`input_audit` 记录训练集、验证集、优化器配置、case metadata 和 prompt 源文件的稳定 SHA-256 与字节数。即使报告脱离工作区，也能复现实验输入。

`audit.cost` 将估算成本拆分为 optimizer 与 evaluation 两部分，并把总成本传入同一个可配置预算 gate。在 fake/trace mode 中，两部分都是确定性的；除了专门的 `cost_exceeded` 场景外通常为 0。

`failure_attribution.self_check` 会把归因结果和 `case_meta.json` 中公开 gold labels 对比。对于无 key 黑盒路径无法自然产生的类别，例如 tool-call 和 LLM-rubric failures，`fake_attribution_hints` 提供显式 fake-judge hint。报告会标记这些 `hint_assisted` case，并单独报告 rule-only accuracy。

已提交的示例输出位于 `sample_outputs/`。

JSON 报告同时包含人类可读原因和结构化 gate checks：

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

## Gate 决策契约

候选只有在所有配置的 gate check 都通过时才会被接受：

| Check | 接受条件 | 拒绝条件 |
| --- | --- | --- |
| `min_validation_score_delta` | 验证集分数提升大于等于 `gate.min_validation_score_delta`。 | 验证集分数提升低于阈值。 |
| `no_new_hard_fail` | 没有验证集 case 从 pass 变为 fail，除非 `gate.allow_new_hard_fail` 为 true。 | 不允许新增 hard fail 时，至少一个验证集 case 变成新失败。 |
| `critical_case_regression` | `gate.critical_case_ids` 中没有 case 分数下降。 | 任意关键验证 case 退化。 |
| `overfitting_guard` | 验证集提升，或训练集没有提升。 | 训练集提升但验证集没有提升。 |
| `max_cost_usd` | 估算候选成本小于等于 `gate.max_cost_usd`。 | 估算成本超过预算。 |

该契约以验证集为优先：仅有训练集提升不能证明候选可接受，隐藏样本也应遵循相同的结构化决策规则。

## Case 覆盖

十个示例 case 超过最低要求，并覆盖所需场景：

| Case | Split | 预期行为 |
| --- | --- | --- |
| `train_refund_double_charge` | train | 候选修复 baseline 失败。 |
| `train_password_reset` | train | 已经通过，候选保持不变。 |
| `train_legacy_sync` | train | 候选没有帮助。 |
| `train_plan_question` | train | 稳定的非目标计费问题保持通过。 |
| `train_policy_tool_missing` | train | 使用不完整 JSON response 覆盖 tool lookup failure 归因。 |
| `val_vip_refund` | validation | 候选带来新的通过。 |
| `val_plan_question` | validation | 候选没有影响。 |
| `val_checkout_outage` | validation | 候选使关键 case 退化。 |
| `val_mobile_crash` | validation | 稳定的技术排障 case 保持通过。 |
| `val_rubric_tone_fail` | validation | 确定性 rubric failure 归因保持失败。 |

默认 fake candidate 会提升训练集分数，但验证集整体没有提升，并且引入一个新的 hard fail，因此 gate 拒绝候选并记录过拟合原因。

## 需求映射

| #91 要求 | 实现 |
| --- | --- |
| Baseline train/validation evaluation | `run_pipeline.py::evaluate_dataset` 使用 `AgentEvaluator.get_executer(...).get_result()`。 |
| Per-case metric、pass/fail、reason、trace | 报告 case 包含 query、expected、actual、metric details、status、failure types 和 reason。 |
| Failure attribution clustering | `attribute_failure` 和 `build_failure_stats`。 |
| 完整归因 taxonomy | 公开样例覆盖 `final_response_mismatch`、`tool_call_error`、`parameter_error`、`llm_rubric_not_met`、`knowledge_recall_insufficient`、`format_violation`。 |
| 归因准确率 guard | `case_meta.json` 和 `failure_attribution.self_check` 校验公开标签，并标记 fake hint-assisted case。 |
| Prompt optimization target | `TargetPrompt` 注册 `router_prompt`、`system_prompt`、`skill_prompt`。 |
| AgentOptimizer 或等价扩展 | `--mode optimizer` 委托 `AgentOptimizer`；fake/trace mode 为无 key CI 使用确定性候选生成。 |
| Candidate validation 和 per-case delta | `build_case_deltas` 标记 `new_pass`、`new_fail`、`score_improved`、`score_regressed`、`unchanged`。 |
| 可配置 gate | `optimizer.json.gate` 和 `apply_gate`，包含结构化 `gate.checks`。 |
| 过拟合拒绝 | 默认 `--scenario overfit` 提升训练集但因验证集无收益和关键 case 退化被拒绝。 |
| 成本预算拒绝 | `--scenario cost_exceeded` 质量提升但失败于 `max_cost_usd`。 |
| 审计产物 | `input_audit`、带 diff preview 的 `prompt_audit`、`optimization_rounds`、`candidate_prompts/`、原始 `eval_*` 输出、配置快照、JSON/Markdown 报告。 |
| 无 API key 模式 | `--mode fake` 和 `--mode trace`。 |
| 6 个公开 case | `train.evalset.json` 有 5 个 case，`val.evalset.json` 有 5 个 case。 |

## 设计说明

本示例把 `AgentEvaluator.get_executer(...).get_result()` 作为唯一评测事实来源，分别对训练集和验证集生成逐 case 记录，包含输入、期望输出、实际输出、metric 分数、pass/fail、失败类型和原因。失败归因采用可复现规则：JSON 解析失败或缺字段归为格式不符合要求，category 不一致归为知识召回不足，priority 不一致归为参数错误，action 不一致归为最终回复不匹配；对必须依赖真实工具轨迹或 LLM rubric judge 的类别，fake mode 通过 `case_meta.json` 的 `fake_attribution_hints` 标记 `tool_call_error` 和 `llm_rubric_not_met`，并在 self-check 中区分 hint-assisted 与 rule-only 准确率，避免把公开标签直接当成黑盒判断结果。

候选 prompt 不会直接写回源文件，而是先在验证集回归，并和 baseline 做逐 case delta，标记新增通过、新增失败、分数提升、分数下降和无变化。gate 由 `optimizer.json` 配置，检查验证集总分提升阈值、是否新增 hard fail、关键 case 是否退化以及成本预算；额外防过拟合规则是训练集提升但验证集没有提升时拒绝候选。这样即使优化器记住训练失败样例，只要验证集没有真实收益，或者牺牲了关键 case，也不会进入可接受状态。

审计产物包括配置快照、随机种子、候选 prompt 文件、`optimization_rounds`、原始 evaluator 输出、JSON 报告和 Markdown 报告，便于完整复现实验配置，也便于后续 CI 读取结构化字段。`prompt_audit` 记录源路径、baseline/candidate 哈希、字符数和是否变更，reviewer 可以把分数变化追溯到具体 prompt。trace mode 会生成 `eval_mode: "trace"` 数据并回放 `actual_conversation`，保证无 API key 环境也能复现 baseline、归因、优化、验证、gate 和审计闭环。

# Evaluation + Optimization Loop

This example demonstrates a reproducible pipeline that connects evaluation, failure attribution, prompt optimization, validation regression checks, gate decisions, and audit reports.

It is intentionally different from `ci_integration/`: CI integration focuses on PR gates and nightly write-back, while this example focuses on the complete review loop before deciding whether a candidate prompt is worth accepting.

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

- `overfit` is the default: train improves, validation does not improve, and a critical case regresses, so the gate rejects the candidate.
- `accepted`: train and validation both improve with no new hard fail, so the gate accepts the candidate.
- `cost_exceeded`: quality improves, but the estimated cost exceeds `gate.max_cost_usd`, so the gate rejects the candidate.

For CI-style scripts, add `--ci-exit-code`. Normal demo runs always exit 0 after writing the report; CI mode exits 0 for accepted candidates and 1 for rejected candidates:

```bash
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario accepted --ci-exit-code
PYTHONPATH=../../.. python run_pipeline.py --mode fake --scenario overfit --ci-exit-code
```

Trace replay mode also runs without model credentials. It first records fake outputs into generated `eval_mode: "trace"` evalsets, then evaluates those `actual_conversation` records without invoking `call_agent`:

```bash
PYTHONPATH=../../.. python run_pipeline.py --mode trace
```

The optional real optimizer path delegates candidate search to `AgentOptimizer.optimize` and requires the normal optimization dependencies and model environment variables:

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

The fake mode still uses `AgentEvaluator` for all train and validation scoring. Only candidate generation is deterministic, so the example can run in CI or on a laptop without model credentials.

At startup, the pipeline validates that all required input files exist, train and validation evalsets are distinct and non-empty, gate keys are present, critical case IDs refer to validation cases, and evaluator parallelism is positive.

Fake judge behavior is implemented by local exact-match final-response scoring (`final_response_avg_score`). The report records this as `run.judge_mode = "local_exact_match_fake_judge"`, so the evaluation path does not need an LLM judge or API key.

For tool-only and rubric-only failure classes, fake judge mode uses deterministic attribution hints from `case_meta.json`. This lets the no-key example exercise the full attribution taxonomy without requiring real tool execution or an LLM rubric judge.

## Outputs

Each run writes to `runs/<mode>_<scenario>_<timestamp>/` by default:

| File or directory | Contents |
| --- | --- |
| `optimization_report.json` | Machine-readable baseline, candidate, per-round prompts, delta, attribution, gate, and audit payload. |
| `optimization_report.md` | Bilingual human-readable report for review. |
| `candidate_prompts/` | Candidate prompt text per target field. |
| `eval_*` | Raw `AgentEvaluator` result files for every phase. |
| `config.snapshot.json` | Reproducible copy of the input config. |
| `eval_metrics.snapshot.json` | Evaluator-compatible metric config extracted from `optimizer.json.evaluate`. |

The report also includes `prompt_audit`, which records each target prompt's source path, baseline SHA-256, candidate SHA-256, character counts, and whether the candidate changed the prompt. Each prompt audit entry also includes a capped unified diff preview, so reviewers can see what changed without opening the candidate prompt files. This makes the score-to-prompt relationship auditable even when reports are copied outside the run directory.

`input_audit` records stable SHA-256 hashes and byte counts for the train evalset, validation evalset, optimizer config, case metadata, and prompt source files. This makes a report reproducible even after it is detached from the working tree.

`audit.cost` splits the total estimated spend into optimizer and evaluation components, then applies the same total to the configurable budget gate. In fake/trace mode both components are deterministic and normally zero except for the dedicated `cost_exceeded` scenario.

`failure_attribution.self_check` compares attribution results against the public gold labels in `case_meta.json`. Gold labels live in `expected_failure_types`. For categories that cannot naturally arise in the no-key black-box path, such as tool-call and LLM-rubric failures, `fake_attribution_hints` provides an explicit fake-judge hint. The report marks those cases as `hint_assisted` and also reports rule-only accuracy separately. This is not a hidden-set proof, but it provides a local accuracy guard for the bundled examples and fails report validation if the labeled-case accuracy drops below 75%.

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

This contract is intentionally validation-first: training improvements alone never justify acceptance, and hidden samples are expected to follow the same structured decision rules.

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

The default fake candidate improves train score but does not improve validation overall because it also introduces a new hard fail. The gate therefore rejects the candidate and records the overfitting reason.

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

This example treats `AgentEvaluator.get_executer(...).get_result()` as the single source of evaluation truth. It generates per-case records for train and validation with input, expected output, actual output, metric score, pass/fail, failure types, and reasons. Failure attribution is reproducible: JSON parsing failures or missing fields become format violations, category mismatches become insufficient knowledge recall, priority mismatches become parameter errors, and action mismatches become final response mismatches. For categories that require real tool traces or an LLM rubric judge, fake mode uses `fake_attribution_hints` from `case_meta.json` to mark `tool_call_error` and `llm_rubric_not_met`, while self-check reports hint-assisted and rule-only accuracy separately.

Candidate prompts are not written back to source files directly. They are first validated on the validation split and compared with baseline per-case deltas: new pass, new fail, score improved, score regressed, and unchanged. The gate is configured in `optimizer.json` and checks validation score delta, new hard fails, critical-case regressions, and cost budget. The extra overfitting guard rejects candidates when train improves but validation does not, so memorizing train failures cannot pass without real validation benefit.

Audit artifacts include config snapshots, random seed, candidate prompt files, `optimization_rounds`, raw evaluator outputs, JSON report, and Markdown report. `prompt_audit` records source paths, baseline/candidate hashes, character counts, and change flags, so reviewers can trace score changes back to specific prompt edits. Trace mode generates `eval_mode: "trace"` data and replays `actual_conversation`, which keeps the baseline, attribution, optimization, validation, gate, and audit loop reproducible without an API key.
