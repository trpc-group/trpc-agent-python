# Evaluation + Optimization 闭环报告

- **决策**：❌ 拒绝 (REJECT)
- **结论**：拒绝候选：命中规则 ['min_val_score_delta']。
- 运行模式：`real` ｜ seed：`42` ｜ 耗时：8.4s
- 时间：2026-07-01T20:17:13 → 2026-07-01T20:17:21

## 1. 分数总览

| 数据集 | baseline 通过 | baseline 均分 | candidate 通过 | candidate 均分 | Δ均分 |
|---|---|---|---|---|---|
| 训练集 | 2/3 | 0.667 | 2/3 | 0.667 | +0.000 |
| 验证集 | 3/3 | 1.000 | 3/3 | 1.000 | +0.000 |

## 2. Baseline 失败归因

**训练集** 失败聚类：{'最终回复不匹配': 1}
- `train_discount_shirt` → **最终回复不匹配**（llm）：agent 未给出正确计算结果，直接拒绝回答，与期望答案不符。
  - 关键轨迹：final_response:抱歉，我暂时无法解答这道题。

**验证集** 失败聚类：无失败

## 3. 候选验证 · 逐 case delta

| case | baseline | candidate | Δ分 | 状态 |
|---|---|---|---|---|
| `val_add_class` | PASS | PASS | +0.000 | ⚪ 不变 |
| `val_mul_box` | PASS | PASS | +0.000 | ⚪ 不变 |
| `val_add_orange` | PASS | PASS | +0.000 | ⚪ 不变 |

- 新增通过：无
- 新增失败：无

## 4. 门控决策明细

| 规则 | 结果 | 说明 |
|---|---|---|
| `min_val_score_delta` | ❌ | 验证集平均分 delta=+0.0000，阈值 ≥ +0.0500 |
| `forbid_new_hard_fail` | ✅ | 新增失败 case=无 |
| `key_cases_no_regression` | ✅ | 关键 case=['val_add_class']，其中退化=无 |
| `cost_within_budget` | ✅ | 候选成本=$0.0000，预算 ≤ $1.0000 |

## 5. 每轮候选审计

- （优化器未产出任何轮次；候选=baseline）

> 每轮候选 prompt 全文见 `optimization_report.json` 的 `candidate.rounds_detail`。

## 6. 候选与成本审计

- 优化状态：`SUCCEEDED` ｜ stop_reason：`required_metrics_passing`
- 被改写字段：[] ｜ 轮数：0
- 成本：$0.000000 ｜ 优化耗时：2.0019s
- 后端：`real`

> 候选 prompt 全文与逐 case 明细见 `optimization_report.json`。
