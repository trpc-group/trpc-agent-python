# Evaluation + Optimization Pipeline (eval_optimize_loop)

构建"评测 - 失败归因 - prompt 优化 - 回归验证 - 产物审计"的自动闭环 pipeline。

## 运行方式

### Trace mode（无需 API Key，<1 秒）

```bash
cd examples/optimization/eval_optimize_loop
python run_pipeline.py
```

未设置 `TRPC_AGENT_API_KEY` 时自动使用预录制轨迹，用于验证 Pipeline 结构。

### Live mode（需要 DeepSeek API Key）

```bash
cd examples/optimization/eval_optimize_loop
set TRPC_AGENT_API_KEY=sk-xxx
set TRPC_AGENT_BASE_URL=https://api.deepseek.com/v1
set TRPC_AGENT_MODEL_NAME=deepseek-v4-flash
python run_pipeline.py
```

## Pipeline 流程

```
Stage 1  基线评测        Baseline(train+val) with defective prompt
Stage 2  失败归因        按类型聚类: hallucination / tool_call_error / 
                         missing_information / overgeneralization 等
Stage 3  优化            基于训练集失败诊断, 定向修改 system prompt
Stage 4  候选验证        用 candidate prompt 重新跑 val set
Stage 5  Delta + Gate    对比基线 vs 候选, 5 项检查决定 ACCEPT/REJECT
Stage 6  报告生成        JSON + Markdown 双格式
```

## 目录结构

```
examples/optimization/eval_optimize_loop/
├── README.md
├── run_pipeline.py                # 主入口 (自动检测 trace/live mode)
├── pipeline_config.json           # Gate 配置
├── pipeline/                      # Pipeline 模块
│   ├── config.py                  # 数据模型
│   ├── evaluator.py               # Stage 1: 评测封装
│   ├── attributor.py              # Stage 2: 失败归因
│   ├── orchestrator.py            # Stage 3-4: 优化+候选验证
│   ├── comparator.py              # Stage 5: Delta 对比
│   ├── gate.py                    # Stage 5: Gate 决策
│   └── reporter.py                # Stage 6: 报告生成
├── agent/
│   ├── agent.py                   # 购物助手 Agent (4 个工具)
│   ├── config.py                  # 模型配置
│   ├── prompts/
│   │   ├── system.md              # 系统 prompt (baseline)
│   │   └── skill.md               # 技能 prompt (baseline, defective)
│   ├── train.evalset.json         # 训练集 (6 cases)
│   ├── val.evalset.json           # 验证集 (6 cases)
│   ├── trace_*.evalset.json       # Trace 模式预录制轨迹
│   ├── test_config.json           # Live 评测指标 (llm_rubric_response)
│   ├── test_config_trace.json    # Trace 评测指标 (final_response_avg_score)
│   └── optimizer.json             # 优化器配置
└── output/
    ├── trace_mode/                # Trace 模式输出
    │   ├── optimization_report.json / .md
    │   ├── baseline_train_detail.json
    │   ├── baseline_val_detail.json
    │   └── candidate_val_detail.json
    └── live_mode/                 # Live 模式输出
        ├── optimization_report.json / .md
        ├── baseline_train_detail.json
        ├── baseline_val_detail.json
        ├── candidate_val_detail.json
        └── optimizer_output/
            └── optimizer_detail.json
```

## 6+6 条测试用例设计

**Agent**: 购物助手，4 个工具: `get_product_price`, `check_stock`, `get_discount`, `get_shipping`

### 训练集 (6 cases)

| ID | 问题类型 | 失败类型 |
|----|---------|---------|
| `train_001` | 价格+库存+折扣(三工具联合) | hallucination, tool_call_error |
| `train_002` | 双城价格对比+计算差值 | hallucination, tool_call_error, missing_information |
| `train_003` | 配送查询(单工具) | hallucination, tool_call_error |
| `train_004` | 多商品折后总价(四工具联合) | hallucination, tool_call_error, missing_information |
| `train_005` | 价格+库存+折扣(三工具联合) | hallucination, tool_call_error |
| `train_006` | 配送+折扣(双工具联合) | hallucination, tool_call_error, missing_information |

### 验证集 (6 cases)

| ID | 问题类型 | 基线 | 候选 | 说明 |
|----|---------|:--:|:--:|------|
| `val_001` | 价格+库存+折扣(泛化) | FAIL | PASS | 换城市+水果 |
| `val_002` | 折扣查询(稳定性) | FAIL | PASS | 单工具 |
| `val_003` | 纯库存查询 | **PASS** | **FAIL** | 过拟合探针: 候选多调了价格工具 |
| `val_004` | 双城对比+计算(推理) | FAIL | PASS | 含计算 |
| `val_005` | 多步计算(折扣+运费) | FAIL | PASS | 多工具联合 |
| `val_006` | 配送+折扣(不可配送) | FAIL | PASS | 边缘 case |

## Baseline 失败类型

| 类型 | 说明 | 出现次数 |
|------|------|:---:|
| `hallucination` | 不调工具,凭经验编造数字 | 11 |
| `tool_call_error` | 完全不调用工具 | 11 |
| `missing_information` | 缺少精确数字,用模糊词代替 | 4 |

根因: baseline skill prompt 指示模型"没有见过的问题不调用工具,套用训练经验作答"。

## Gate 决策

Accept 条件 (全部满足才接受):

1. `min_improvement`: val pass_rate 提升 >= 10%
2. `no_hard_regression`: 无 PASS -> FAIL case
3. `key_cases_ok`: 指定关键 case 通过
4. `cost_ok`: 总成本 <= budget
5. `per_metric_floor`: 所有 metric >= floor

当 val_003 出现 overgeneralization (纯库存查询时多调了价格工具,PASS -> FAIL), Gate 会正确 REJECTED。

## 验收标准对照

| 标准 | 状态 |
|------|:---:|
| 12 条 case 可运行 | OK |
| 生成完整优化报告 (JSON + MD + 逐 case trace) | OK |
| 失败归因: hallucination / tool_call_error / missing_information / overgeneralization | OK |
| 过拟合场景拒绝 (val_003) | OK |
| Trace mode <= 1 秒 | OK |
| 报告含 baseline/candidate/delta/gate/reason + prompt 对比 | OK |
