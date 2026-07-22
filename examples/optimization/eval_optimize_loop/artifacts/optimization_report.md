# 评测 - 优化闭环报告（optimization_report）

- 模式: `fake_model + fake_judge + call_agent (no API key required)`
- 复现种子: `42`
- 耗时: `0.008s`
- 配置: `/home/ybj/trpc-agent-python/examples/optimization/eval_optimize_loop/config/optimizer.json`

## 1. Baseline 评测
- 训练集通过率: **0.0**
- 验证集通过率: **0.3333**

### 逐 case（baseline）
- train `train_weather`: FAIL (score=0.0)
- train `train_format`: FAIL (score=0.0)
- train `train_dead`: FAIL (score=0.0)
- val `val_robust`: PASS (score=1.0)
- val `val_format`: FAIL (score=0.0)
- val `val_weather`: FAIL (score=0.0)

### 失败归因统计（baseline）
- `final_mismatch`: 1
- `tool_call_error`: 2
- `format_error`: 2

## 2. 候选验证与 Gate 决策

### 候选 `candidate_1` — ✅ 接受
- 训练集通过率: 0.3333 | 验证集通过率: 0.6667
- 验证集: 0.3333 -> 0.6667 (improvement=0.3333)
  - 验证集总分提升 0.3333 -> 0.6667 (++0.3333)，无新增 hard fail、关键 case 未退化、成本在预算内。
  - case `val_format`: kept_fail (base=F, cand=F)
  - case `val_weather`: new_pass (base=F, cand=P)

### 候选 `candidate_2` — ❌ 拒绝
- 训练集通过率: 0.3333 | 验证集通过率: 0.3333
- 验证集: 0.3333 -> 0.3333 (improvement=0.0)
  - 验证集总分未达提升阈值：0.3333 -> 0.3333 (需提升 >= 0.0100)
  - 出现新增 hard fail（baseline 通过但候选失败）：['val_robust']
  - 关键 case 退化（过拟合信号）：['val_robust']
  - case `val_format`: new_pass (base=F, cand=P)
  - case `val_robust`: new_fail (base=P, cand=F)
  - case `val_weather`: kept_fail (base=F, cand=F)

### 候选 `candidate_3` — ❌ 拒绝
- 训练集通过率: 0.3333 | 验证集通过率: 0.3333
- 验证集: 0.3333 -> 0.3333 (improvement=0.0)
  - 验证集总分未达提升阈值：0.3333 -> 0.3333 (需提升 >= 0.0100)
  - 出现新增 hard fail（baseline 通过但候选失败）：['val_robust']
  - 关键 case 退化（过拟合信号）：['val_robust']
  - case `val_format`: kept_fail (base=F, cand=F)
  - case `val_robust`: new_fail (base=P, cand=F)
  - case `val_weather`: new_pass (base=F, cand=P)

### 候选 `candidate_4` — ❌ 拒绝
- 训练集通过率: 0.0 | 验证集通过率: 0.3333
- 验证集: 0.3333 -> 0.3333 (improvement=0.0)
  - 验证集总分未达提升阈值：0.3333 -> 0.3333 (需提升 >= 0.0100)
  - case `val_format`: kept_fail (base=F, cand=F)
  - case `val_weather`: kept_fail (base=F, cand=F)

## 3. 最终决策
- **接受候选**: `candidate_1`
- 训练集 Δ: 0.3333 | 验证集 Δ: 0.3333

### 被拒绝候选汇总
- `candidate_2` (val=0.3333): 验证集总分未达提升阈值：0.3333 -> 0.3333 (需提升 >= 0.0100); 出现新增 hard fail（baseline 通过但候选失败）：['val_robust']; 关键 case 退化（过拟合信号）：['val_robust']
- `candidate_3` (val=0.3333): 验证集总分未达提升阈值：0.3333 -> 0.3333 (需提升 >= 0.0100); 出现新增 hard fail（baseline 通过但候选失败）：['val_robust']; 关键 case 退化（过拟合信号）：['val_robust']
- `candidate_4` (val=0.3333): 验证集总分未达提升阈值：0.3333 -> 0.3333 (需提升 >= 0.0100)

## 4. 是否值得回写生产？
候选 `candidate_1` 在验证集上提升且无关键 case 退化、无新增 hard fail，建议人工复核后回写 `prompts/baseline_system.md`。
