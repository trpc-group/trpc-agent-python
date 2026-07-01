# Evaluation + Optimization 闭环报告

- **决策**：❌ 拒绝 (REJECT)
- **结论**：拒绝候选：命中规则 ['min_val_score_delta', 'forbid_new_hard_fail', 'key_cases_no_regression']。疑似过拟合（验证集出现退化/新增失败）。
- 运行模式：`fake` ｜ seed：`42` ｜ 耗时：0.01s
- 时间：2026-07-01T20:17:51 → 2026-07-01T20:17:51

## 1. 分数总览

| 数据集 | baseline 通过 | baseline 均分 | candidate 通过 | candidate 均分 | Δ均分 |
|---|---|---|---|---|---|
| 训练集 | 1/3 | 0.333 | 2/3 | 0.667 | +0.333 |
| 验证集 | 2/3 | 0.667 | 2/3 | 0.667 | +0.000 |

> ⚠️ **过拟合信号**：训练集提升 +0.333，验证集却未提升（+0.000）——候选在训练分布上过度特化。

## 2. Baseline 失败归因

**训练集** 失败聚类：{'知识召回不足': 2}
- `train_mul_car` → **知识召回不足**（fake）：agent 声明无法解答，说明缺少对应题型的解题能力（技能/知识缺口）。
  - 关键轨迹：final_response:抱歉，我暂时无法解答这道题。
- `train_discount_shirt` → **知识召回不足**（fake）：agent 声明无法解答，说明缺少对应题型的解题能力（技能/知识缺口）。
  - 关键轨迹：final_response:抱歉，我暂时无法解答这道题。

**验证集** 失败聚类：{'知识召回不足': 1}
- `val_mul_box` → **知识召回不足**（fake）：agent 声明无法解答，说明缺少对应题型的解题能力（技能/知识缺口）。
  - 关键轨迹：final_response:抱歉，我暂时无法解答这道题。

## 3. 候选验证 · 逐 case delta

| case | baseline | candidate | Δ分 | 状态 |
|---|---|---|---|---|
| `val_mul_box` | FAIL | PASS | +1.000 | 🟢 新增通过 |
| `val_add_class` | PASS | FAIL | -1.000 | 🔴 新增失败 |
| `val_add_orange` | PASS | PASS | +0.000 | ⚪ 不变 |

- 新增通过：['val_mul_box']
- 新增失败：['val_add_class']

## 4. 门控决策明细

| 规则 | 结果 | 说明 |
|---|---|---|
| `min_val_score_delta` | ❌ | 验证集平均分 delta=+0.0000，阈值 ≥ +0.0500 |
| `forbid_new_hard_fail` | ❌ | 新增失败 case=['val_add_class'] |
| `key_cases_no_regression` | ❌ | 关键 case=['val_add_class']，其中退化=['val_add_class'] |
| `cost_within_budget` | ✅ | 候选成本=$0.0000，预算 ≤ $1.0000 |

## 5. 每轮候选审计

- **Round 1**：改写字段 ['skill']
  - 脚本化过拟合候选：新增乘法能力 + assume-mul-default 副作用

> 每轮候选 prompt 全文见 `optimization_report.json` 的 `candidate.rounds_detail`。

## 6. 候选与成本审计

- 优化状态：`SUCCEEDED` ｜ stop_reason：`fake_scripted_candidate`
- 被改写字段：['skill'] ｜ 轮数：1
- 成本：$0.000000 ｜ 优化耗时：0.0001s
- 后端：`fake`

> 候选 prompt 全文与逐 case 明细见 `optimization_report.json`。
