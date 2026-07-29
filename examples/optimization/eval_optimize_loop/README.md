# Evaluation + Optimization Pipeline (eval_optimize_loop)

构建"评测 - 失败归因 - prompt 优化 - 回归验证 - 产物审计"的自动闭环 pipeline。

## 运行方式

### Trace mode（无需 API Key，<1 秒）

```bash
cd examples/optimization/eval_optimize_loop
python run_pipeline.py
```

未设置 `TRPC_AGENT_API_KEY` 时自动使用预录制轨迹，用于验证 Pipeline 结构。

### Live mode（需要 OpenAI 兼容 API Key）

DeepSeek 官方 API（已实测）：

```bash
cd examples/optimization/eval_optimize_loop
export TRPC_AGENT_API_KEY=sk-xxx
export TRPC_AGENT_BASE_URL=https://api.deepseek.com
export TRPC_AGENT_MODEL_NAME=deepseek-v4-flash
python run_pipeline.py
```

DeepSeek V4 默认开启 thinking；本示例为 Agent 和 judge 显式关闭 thinking，
避免结构化 judge 输出额度被思考过程耗尽而产生 `NOT_EVALUATED`。

腾讯云 TokenHub：

Linux / macOS:

```bash
cd examples/optimization/eval_optimize_loop
export TRPC_AGENT_API_KEY=sk-xxx
export TRPC_AGENT_BASE_URL=https://tokenhub.tencentmaas.com/v1
export TRPC_AGENT_MODEL_NAME=hy3
python run_pipeline.py
```

Windows PowerShell:

```powershell
cd examples/optimization/eval_optimize_loop
$env:PYTHONIOENCODING = "utf-8"
$env:TRPC_AGENT_API_KEY = "sk-xxx"
$env:TRPC_AGENT_BASE_URL = "https://tokenhub.tencentmaas.com/v1"
$env:TRPC_AGENT_MODEL_NAME = "hy3"
python run_pipeline.py
```

以上示例使用腾讯云 TokenHub 的 OpenAI 兼容接口。`test_config.json`
默认每个 judge 使用 1 个样本，避免 6+6 case 的四次评测超过常见的 60 RPM
限额。模型调用失败或出现 `NOT_EVALUATED` 时 Pipeline 会直接失败，不会用
0 分生成误导性报告。

## Pipeline 流程

```
Stage 1  基线评测        Baseline(train+val) with defective prompt
Stage 2  训练失败归因    仅对 train 按失败类型聚类，val 保持隔离
Stage 3  优化            只基于训练失败及训练工具模式修改 system prompt
Stage 4  候选验证        用 candidate prompt 重新跑 train + val set
Stage 5  Delta + Gate    候选固定后才归因 val，并由 6 项检查决定 ACCEPT/REJECT
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
│   ├── trace_*.evalset.json       # baseline/candidate 的预录制轨迹
│   ├── test_config.json           # Live 指标 (tool trajectory + LLM rubric)
│   ├── test_config_trace.json    # Trace 评测指标 (final_response_avg_score)
│   └── optimizer.json             # 优化器配置
└── output/
    └── <YYYY-MM-DDTHH-MM-SS>/     # 每次运行独立输出
        ├── optimization_report.json / .md
        ├── baseline_train_detail.json
        ├── baseline_val_detail.json
        ├── candidate_train_detail.json
        ├── candidate_val_detail.json
        ├── prompts/               # 本次运行的 prompt 快照
        │   ├── baseline/
        │   └── candidate/
        └── optimizer_output/
            └── optimizer_detail.json
```

Pipeline 不会改写 `agent/prompts/system.md` 或 `skill.md`。Baseline preset 和
候选 prompt 均写入本次运行的 `output/<timestamp>/prompts/`，Agent 仅在本次
进程中读取对应快照。仓库中的 `output/trace_mode/` 与 `output/live_mode/`
仅是随代码提交的最新示例快照；实际运行始终创建新的时间戳目录。

## 样例 case 设计

**Agent**: 购物助手，4 个工具: `get_product_price`, `check_stock`, `get_discount`, `get_shipping`

Live mode 使用 6 条训练 case 和 6 条验证 case。Trace mode 为保证无 Key 时快速、
确定性复现核心决策，使用对应的 3+3 精简轨迹，并额外保存 candidate train 轨迹。

### 训练集 (6 cases)

| ID | 问题类型 | 主要覆盖点 |
|----|---------|---------|
| `train_001` | 价格+库存+折扣(三工具联合) | 多工具完整性 |
| `train_002` | 双城价格对比+计算差值 | 工具参数与推理 |
| `train_003` | 配送查询(单工具) | 单工具稳定性 |
| `train_004` | 多商品折后总价(四工具联合) | 多步计算 |
| `train_005` | 价格+库存+折扣(三工具联合) | 同类任务泛化 |
| `train_006` | 配送+折扣(双工具联合) | 不可配送边界 |

### 验证集 (6 cases)

| ID | 问题类型 | 场景作用 |
|----|---------|------|
| `val_001` | 价格+库存+折扣(泛化) | 换城市与商品验证泛化 |
| `val_002` | 折扣查询(稳定性) | 单工具稳定性 |
| `val_003` | 纯库存查询 | 额外工具调用/过度泛化探针 |
| `val_004` | 双城对比+计算(推理) | 未见组合与计算 |
| `val_005` | 多步计算(折扣+运费) | 多工具联合 |
| `val_006` | 配送+折扣(不可配送) | 不可配送边界 |

实际 PASS/FAIL 必须以当次报告为准，不能在 README 中预设。确定性的 3+3
trace 场景同时覆盖三类验收结果：`train_001/002` 为 FAIL→PASS（可优化成功）；
`val_001` 为 FAIL→FAIL（优化无效）；`val_003` 为 PASS→FAIL（优化后退化）。
汇总上 train 从 33.3% 提升至 100%，val 从 66.7% 下降至 33.3%，因此
`no_overfit` 和 `no_hard_regression` 均拒绝候选。

## 失败归因

归因器综合指标状态、rubric reason、期望/实际工具轨迹和回复启发式信号，可输出
`tool_call_error`、`tool_arg_error`、`missing_information`、
`overgeneralization`、`hallucination`、`format_error` 等类型。FAILED case
若未命中细分类，会落到带解释文本的 fallback，不会从报告中消失。

## Gate 决策

Accept 条件 (全部满足才接受):

1. `min_improvement`: val pass_rate 提升 >= 10%
2. `no_hard_regression`: 无 PASS -> FAIL case
3. `key_cases_ok`: 指定关键 case 通过
4. `cost_ok`: 总成本 <= budget
5. `per_metric_floor`: 所有 metric >= floor
6. `no_overfit`: 不允许 train 达到提升阈值而 val 达到退化阈值

Trace 示例会因 train 提升、val 退化以及 `val_003` PASS→FAIL 被拒绝。Live
mode 不预设结论：若验证集真实提升且无 hard regression，Gate 可以接受，同时
仍可能输出 train-val gap warning。

## 方案设计说明（约 400 字）

Pipeline 先使用 AgentEvaluator 分别执行训练集和验证集基线评测，将每条 case
的指标分、状态、回复与工具轨迹标准化落盘。失败归因优先采用 judge rubric
reason，再比较期望和实际工具名称、参数及回复内容；规则无法细分时使用带说明的
fallback，保证每个 FAILED case 至少有一个可解释标签。优化阶段仅归因训练失败，
并只使用训练 case 的期望工具模式构造候选 TargetPrompt；候选 prompt 固定后才
归因验证失败，确保验证信息不进入优化上下文。随后同时重跑 train 与 val，并由
Comparator 输出 newly_passing、newly_failing、improved、degraded 和 unchanged。Gate
采用全条件通过策略：验证提升达到阈值、无硬回归、关键 case 保持通过、指标不低于
floor、成本不超预算，并在 train 提升而 val 下降时判定过拟合并强制拒绝。Trace
样例通过独立 candidate train/val 轨迹确定性复现该拒绝路径。审计方面，每次运行
写入独立时间戳目录，保存 baseline/candidate prompt 快照、逐 case 结果、delta、
Gate 原因、阶段耗时、seed 和配置；源 prompt 始终只读。模型认证、限流或 judge
失败造成 NOT_EVALUATED 时立即中止，避免把基础设施故障包装成普通 0 分报告。

## 验收标准对照

| 标准 | 状态 |
|------|:---:|
| Live 12 条、Trace 6 条 case 可运行 | OK |
| 生成完整优化报告 (JSON + MD + 逐 case trace) | OK |
| 失败归因: hallucination / tool_call_error / missing_information / overgeneralization | OK |
| Trace 中 train↑、val↓ 过拟合场景拒绝 | OK |
| Trace mode <= 1 秒 | OK |
| 报告含 baseline/candidate/delta/gate/reason + prompt 对比 | OK |
