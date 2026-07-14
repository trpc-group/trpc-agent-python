# Evaluation + Optimization 闭环报告

- **状态**: `accept`  |  **模式**: `trace`  |  **seed**: 42  |  **schema**: eval_optimize_loop.v1
- **选中候选**: `robust`  |  **耗时**: 0.015s

## 1. Baseline

| split | pass_rate | average_score |
|---|---|---|
| train | 0.00 | 0.50 |
| validation | 0.67 | 0.83 |

## 2. 失败归因

覆盖 **4/4** 失败 case（coverage = 100%）。

| 类别 | 数量 |
|---|---|
| `format_violation` | 2 |
| `tool_parameter_error` | 1 |
| `knowledge_recall_insufficient` | 1 |

## 3. 候选决策

| candidate | train Δpr | val Δpr | overfit? | gate | risk |
|---|---|---|---|---|---|
| robust | +1.00 | +0.33 | 否 | ✅ **accept** | low |
| ineffective | +0.00 | +0.00 | 否 | ❌ **reject** | medium |
| overfit | +1.00 | -0.33 | 是 | ❌ **reject** | high |

### `ineffective` 拒绝/复核理由
- ✅ **no_overfit**: ok
- ✅ **no_new_hard_fails**: ok
- ✅ **no_critical_regression**: ok
- ✅ **no_case_regression**: ok
- ❌ **validation_score_improved**: insufficient validation gain
- ✅ **validation_pass_rate_not_worse**: ok
- ✅ **budget_duration**: ok
- ✅ **budget_metric_calls**: ok
- ❌ **tie_policy**: candidate identical to baseline (tie) → reject per tie_policy

### `overfit` 拒绝/复核理由
- ❌ **no_overfit**: overfitting detected
- ❌ **no_new_hard_fails**: 2 newly failing cases
- ❌ **no_critical_regression**: critical cases regressed: ['val_fiction_key']
- ✅ **no_case_regression**: ok
- ❌ **validation_score_improved**: insufficient validation gain
- ❌ **validation_pass_rate_not_worse**: validation pass rate dropped
- ✅ **budget_duration**: ok
- ✅ **budget_metric_calls**: ok
- ✅ **tie_policy**: ok

## 4. 审计

- config_sha256: `a998ffa78f87e673ca005f7c69aa503829c29ed673d7bfa16053066569ae8d60`
- train_sha256: `26d1cda4121ab31080873704ed532b2af105764b8bb26b28ede4170583140968`
- validation_sha256: `9652294ddb58dc465cafb890f10cf581b8063de0cd96dd58a69311508af86df2`
- cost_measurement: `measured_zero_offline`

## 5. 复现

```
python run_pipeline.py --mode trace
```
