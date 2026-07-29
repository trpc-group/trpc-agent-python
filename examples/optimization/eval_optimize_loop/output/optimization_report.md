# Evaluation Optimization Pipeline 报告

## 运行摘要

- 决策：**REJECT**
- 推荐动作：`keep_baseline_prompts`
- 模式：`fake`
- Schema：`eval-optimize-loop-v1`
- 验证集分数变化：0.0000
- 验证集通过率变化：0.0000
- 主要原因：Validation score gain is below the minimum threshold. Candidate introduced new validation failures. Candidate introduced validation regressions. One or more critical validation cases regressed.

## Baseline 表现

- 训练集：2/3 通过，通过率 0.6667
- 验证集：1/3 通过，通过率 0.3333

## 错误归因汇总

### 训练集 baseline

| category | count |
| --- | ---: |
| final_answer_mismatch | 1 |
| tool_call_error | 0 |
| parameter_error | 0 |
| llm_rubric_failed | 0 |
| retrieval_failure | 0 |
| format_error | 0 |
| unknown | 0 |

### 训练集 candidate

| category | count |
| --- | ---: |
| final_answer_mismatch | 0 |
| tool_call_error | 0 |
| parameter_error | 0 |
| llm_rubric_failed | 0 |
| retrieval_failure | 0 |
| format_error | 0 |
| unknown | 0 |

### 验证集 baseline

| category | count |
| --- | ---: |
| final_answer_mismatch | 2 |
| tool_call_error | 0 |
| parameter_error | 0 |
| llm_rubric_failed | 0 |
| retrieval_failure | 0 |
| format_error | 0 |
| unknown | 0 |

### 验证集 candidate

| category | count |
| --- | ---: |
| final_answer_mismatch | 2 |
| tool_call_error | 0 |
| parameter_error | 0 |
| llm_rubric_failed | 0 |
| retrieval_failure | 0 |
| format_error | 0 |
| unknown | 0 |

## 优化过程

- 目标 Prompt：system_prompt, skill
- 优化轮数：1
- 优化成本：0.0
- 随机种子：42

### Round 1

- 修改字段：system_prompt, skill
- 是否接受为候选：True
- 原因：Address failure attribution categories: final_answer_mismatch=3

## 候选验证

- 训练集候选：3/3 通过，通过率 1.0000
- 验证集候选：1/3 通过，通过率 0.3333

## 逐 Case Delta

| split | case id | baseline | candidate | change | failure transition | regression | improvement |
| --- | --- | ---: | ---: | --- | --- | --- | --- |
| train | train_addition_pass | 1.0000 | 1.0000 | unchanged | None -> None | False | False |
| train | train_discount_fail | 0.0000 | 1.0000 | new_pass | final_answer_mismatch -> None | False | True |
| train | train_percent_pass | 1.0000 | 1.0000 | unchanged | None -> None | False | False |
| val | val_multiply_pass | 1.0000 | 0.0000 | new_fail | None -> final_answer_mismatch | True | False |
| val | val_percent_fail | 0.0000 | 0.0000 | unchanged | final_answer_mismatch -> final_answer_mismatch | False | False |
| val | val_water_fail | 0.0000 | 1.0000 | new_pass | final_answer_mismatch -> None | False | True |

## Gate 决策

- 最终决策：**REJECT**
- 推荐动作：`keep_baseline_prompts`
- 拒绝/接受理由：Validation score gain is below the minimum threshold. Candidate introduced new validation failures. Candidate introduced validation regressions. One or more critical validation cases regressed.
- 最小验证集分数提升：0.05
- 允许新增失败：False
- 允许验证集回归：False
- 关键 Case：val_multiply_pass

| rule | passed | severity | message |
| --- | --- | --- | --- |
| validation_score_gain | False | required | Validation score gain is below the minimum threshold. |
| validation_pass_rate_gain | True | required | Validation pass-rate gain satisfies the minimum threshold. |
| new_failures | False | required | Candidate introduced new validation failures. |
| validation_regressions | False | required | Candidate introduced validation regressions. |
| critical_case_regressions | False | required | One or more critical validation cases regressed. |
| cost_budget | True | required | Candidate cost is within the configured budget. |
| overfit_detection | True | required | No train-only overfit pattern was detected. |

## 元数据与复现

- 示例根目录：`.`
- 复现命令：`python run_pipeline.py --mode fake`
- 输出 JSON：`output/optimization_report.json`
- 输出 Markdown：`output/optimization_report.md`

| key | value |
| --- | --- |
| example_root | . |
| output_dir | output |
