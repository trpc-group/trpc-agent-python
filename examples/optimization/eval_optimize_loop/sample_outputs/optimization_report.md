# 优化报告

决策：**拒绝 (REJECTED)**

## 分数

| 数据集 | Baseline | Candidate | Delta |
| --- | ---: | ---: | ---: |
| 训练集 | 0.4000 | 0.6000 | +0.2000 |
| 验证集 | 0.6000 | 0.6000 | +0.0000 |

## Gate 原因

- validation score delta +0.0000 is below required +0.1000
- new hard fail(s) are not allowed: val_checkout_outage
- critical case regression(s): val_checkout_outage
- overfitting guard triggered: train improved while validation did not improve

## 验证集 Case Delta

| Case | Baseline | Candidate | Delta | Outcome |
| --- | ---: | ---: | ---: | --- |
| val_checkout_outage | 1.0000 | 0.0000 | -1.0000 | new_fail |
| val_mobile_crash | 1.0000 | 1.0000 | +0.0000 | unchanged |
| val_plan_question | 1.0000 | 1.0000 | +0.0000 | unchanged |
| val_rubric_tone_fail | 0.0000 | 0.0000 | +0.0000 | unchanged |
| val_vip_refund | 0.0000 | 1.0000 | +1.0000 | new_pass |

## 失败归因

- final_response_mismatch: 4
- format_violation: 1
- knowledge_recall_insufficient: 2
- llm_rubric_not_met: 1
- parameter_error: 3
- tool_call_error: 1
- self-check 准确率：1.0 (5/5)
- hint-assisted cases: 2 | rule-only accuracy: 1.0

## 候选 Prompt

- candidate_id: `candidate_refund_rule_overfit`
- rationale: Adds refund handling learned from train failures but intentionally omits the outage guard to demonstrate overfitting rejection.
- validation pass rate: 0.6000
- baseline validation pass rate: 0.6000
- audited rounds: 2

## Prompt 审计

- `router_prompt`: changed=True baseline=339b66e767b4 candidate=c315899809bf diff_lines=9
- `system_prompt`: changed=True baseline=c557bea101c2 candidate=41a629093df3 diff_lines=9
- `skill_prompt`: changed=True baseline=0494b66f4d69 candidate=909f172b7663 diff_lines=11

## 输入审计

- `train_evalset`: 2cb167dff188 (3330 bytes)
- `validation_evalset`: 702a90d3ecf2 (3256 bytes)
- `optimizer_config`: 3ea2fd21f077 (1586 bytes)
- `case_meta`: d87c7127e9f6 (1221 bytes)

## 复现命令

```bash
cd examples/optimization/eval_optimize_loop
PYTHONPATH=../../.. python run_pipeline.py --mode fake
```

# Optimization Report

Decision: **REJECTED**

## Scores

| Split | Baseline | Candidate | Delta |
| --- | ---: | ---: | ---: |
| train | 0.4000 | 0.6000 | +0.2000 |
| validation | 0.6000 | 0.6000 | +0.0000 |

## Gate Reasons

- validation score delta +0.0000 is below required +0.1000
- new hard fail(s) are not allowed: val_checkout_outage
- critical case regression(s): val_checkout_outage
- overfitting guard triggered: train improved while validation did not improve

## Validation Case Delta

| Case | Baseline | Candidate | Delta | Outcome |
| --- | ---: | ---: | ---: | --- |
| val_checkout_outage | 1.0000 | 0.0000 | -1.0000 | new_fail |
| val_mobile_crash | 1.0000 | 1.0000 | +0.0000 | unchanged |
| val_plan_question | 1.0000 | 1.0000 | +0.0000 | unchanged |
| val_rubric_tone_fail | 0.0000 | 0.0000 | +0.0000 | unchanged |
| val_vip_refund | 0.0000 | 1.0000 | +1.0000 | new_pass |

## Failure Attribution

- final_response_mismatch: 4
- format_violation: 1
- knowledge_recall_insufficient: 2
- llm_rubric_not_met: 1
- parameter_error: 3
- tool_call_error: 1
- self-check accuracy: 1.0 (5/5)
- hint-assisted cases: 2 | rule-only accuracy: 1.0

## Candidate

- candidate_id: `candidate_refund_rule_overfit`
- rationale: Adds refund handling learned from train failures but intentionally omits the outage guard to demonstrate overfitting rejection.
- validation pass rate: 0.6000
- baseline validation pass rate: 0.6000
- audited rounds: 2

## Prompt Audit

- `router_prompt`: changed=True baseline=339b66e767b4 candidate=c315899809bf diff_lines=9
- `system_prompt`: changed=True baseline=c557bea101c2 candidate=41a629093df3 diff_lines=9
- `skill_prompt`: changed=True baseline=0494b66f4d69 candidate=909f172b7663 diff_lines=11

## Input Audit

- `train_evalset`: 2cb167dff188 (3330 bytes)
- `validation_evalset`: 702a90d3ecf2 (3256 bytes)
- `optimizer_config`: 3ea2fd21f077 (1586 bytes)
- `case_meta`: d87c7127e9f6 (1221 bytes)

## Reproduce

```bash
cd examples/optimization/eval_optimize_loop
PYTHONPATH=../../.. python run_pipeline.py --mode fake
```
