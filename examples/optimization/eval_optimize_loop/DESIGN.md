# 方案设计（Issue #91：Evaluation + Optimization 闭环）

## Champion–Challenger 与数据隔离

管线把当前源 prompt 定义为 Champion，把 `AgentOptimizer.optimize(update_source=False)`
产出的最佳 prompt（或 fake 模式下的显式候选）定义为 Challenger。训练集只供优化器发现
问题；验证集是独立裁判，只参与回归与门禁，失败细节不回喂优化器。fake 模式由 prompt 内
公开的 `FAKE_CONTROLS` 控制项生成可评测轨迹，仅验证流程本身，不证明泛化能力。

## 失败归因方法

归因完全基于 evaluator 证据，不读场景标签：先用 `error_message` / `NOT_EVALUATED`
区分 infrastructure_failure；再按失败 metric 名与 reason 识别 knowledge_fail /
rubric_fail；然后对比期望与实际工具轨迹区分 tool_call_error（缺调用或调错工具）与
param_error（同名工具参数不一致，附参数 diff）；最后对比 actual/expected 文本，核心
数值一致判 format_fail，否则 reply_mismatch；证据不足时输出 insufficient_evidence，
不臆造类别。每个失败 case 至少给出一条可解释原因和原始证据。

## 接受策略与防过拟合

Gate 为纯规则、逐条单测：G1 验证集提升 ≥ min_val_lift；G2 train 升而 val 降视为过拟
合，直接拒绝；G3 不新增 high-risk hard fail；G4 protected case 不退化；G5 slice 平均
退化不超 tolerance；G6 成本证据完整且不超预算，成本未知一律禁止自动接受；G7 epsilon
内的微小波动不算有效提升。防过拟合由 train/val 物理隔离 + G2 + G4/G5 共同保证。

## 产物审计

每次运行生成唯一 `runs/<UTC时间>-<随机后缀>/` 目录，`frozen.json` 冻结 prompt、数据
集、评测/优化/gate 配置与随机种子的 sha256；落盘 Champion/Challenger 快照、完整
evaluator 结果、优化轮次、调用审计、耗时与成本。`optimization_report.{json,md}` 记录
baseline / candidate 分数、逐 case delta、归因统计、gate 决策与理由、复现命令。默认
dry-run；仅 ACCEPT 且显式 `--apply` 时经 `TargetPrompt` 原子写回，优化失败也会落盘可
审计的 REJECT 报告。
