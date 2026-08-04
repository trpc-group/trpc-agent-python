# Evaluation + Optimization 自动回归闭环

这个示例演示如何把 Prompt 优化做成一条可复现、可审计的工程链路：先评测 baseline，生成候选 Prompt，再分别执行训练集和验证集回归，最后由独立 Gate 决定是否接受候选并输出完整报告。

```text
Baseline Prompt
      │
      ├─ Train Evaluation
      ├─ Validation Evaluation
      ▼
Candidate Provider / AgentOptimizer
      │
      ├─ Candidate Train Evaluation
      ├─ Candidate Validation Evaluation
      ▼
Attribution + Case Diff + Gate
      │
      ├─ ACCEPT / REJECT
      ├─ optimization_report.json / .md
      └─ guarded writeback（默认关闭）
```

## 运行模式

| 模式 | 是否需要 API Key | 用途 |
|---|---:|---|
| `offline` | 否 | 使用 SDK `LlmAgent`、`Runner` 和确定性 Fake Model 完整执行 Agent 链路，适合第一次体验和本地验收。 |
| `trace` | 否 | 使用 SDK `eval_mode="trace"` 回放已保存轨迹，不运行业务模型和候选生成器，适合稳定回归。 |
| `real` | 是 | 使用真实业务模型和 `AgentOptimizer` 生成候选，适合集成验证；必须显式传入 `--run-real`。 |

Fake Model 只是 offline 模式中的模型实现，不是第四种模式。三个模式共享相同的评测标准化、失败归因、Case Diff、Gate、报告和审计链路。

## 一分钟快速运行

以下命令均从仓库根目录执行。

### 1. 安装依赖

```bash
python -m pip install -e ".[eval,optimize]"
```

如果仓库已经创建 `.venv` 并安装依赖，可以直接使用 `.venv/bin/python` 代替 `python`。

### 2. 无 API Key 运行 improve

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --run-id quickstart_improve \
  --scenario improve
```

预期看到：

```text
Baseline validation: 1/3 passed, average score=0.333
Candidate validation: 3/3 passed, average score=1.000
Gate decision: ACCEPT
Writeback: SKIPPED (disabled)
```

命令最后会打印三个产物路径：

```text
examples/optimization/eval_optimize_loop/runs/quickstart_improve/report/optimization_report.json
examples/optimization/eval_optimize_loop/runs/quickstart_improve/report/optimization_report.md
examples/optimization/eval_optimize_loop/runs/quickstart_improve/report/artifact_index.json
```

优先打开 `optimization_report.md` 查看人类可读的决策说明，再通过 JSON 报告检查逐 Case、逐 Metric 证据。

## 三种确定性场景

offline 模式内置三种候选，用于快速观察 Gate 的不同决策：

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --run-id offline_improve \
  --scenario improve

python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --run-id offline_no_improvement \
  --scenario no_improvement

python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --run-id offline_overfit \
  --scenario overfit
```

| 场景 | 训练集 | 验证集 | Gate |
|---|---|---|---|
| `improve` | 提升 | 提升 | `ACCEPT` |
| `no_improvement` | 不变 | 不变 | `REJECT` |
| `overfit` | 提升 | 退化 | `REJECT` |

Gate 拒绝属于正常业务结果，CLI 仍会生成报告并以成功进程结束；只有配置、评测、优化、报告或安全校验异常才属于运行失败。

## Trace 回放

Trace 模式使用已保存的 baseline/candidate 轨迹驱动同一条归因、Diff、Gate 和报告链路：

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --config examples/optimization/eval_optimize_loop/configs/trace.json \
  --run-id trace_improve \
  --scenario improve
```

另外两个场景只需修改 `--scenario`：

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --config examples/optimization/eval_optimize_loop/configs/trace.json \
  --run-id trace_no_improvement \
  --scenario no_improvement

python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --config examples/optimization/eval_optimize_loop/configs/trace.json \
  --run-id trace_overfit \
  --scenario overfit
```

Trace 模式不会运行 Agent、Model 或 Candidate Provider，也不会写回源 Prompt。它适合保存真实运行轨迹后，在无网络、无 API Key 的环境中执行稳定回归。

## 真实模型模式

真实模式会产生外部 API 调用和费用。先配置 OpenAI-compatible 业务模型连接：

```bash
export TRPC_AGENT_API_KEY="<your-api-key>"
export TRPC_AGENT_BASE_URL="<your-base-url>"
export TRPC_AGENT_MODEL_NAME="<your-business-model>"
```

再显式启动：

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --config examples/optimization/eval_optimize_loop/configs/real.json \
  --run-id real_smoke \
  --run-real \
  --optimizer-model-name "<your-reflection-model>" \
  --max-candidate-proposals 1
```

关键保护：

- 缺少 `--run-real` 时，CLI 在任何真实请求前退出；
- 业务模型凭据只从环境读取，不写入正式报告；
- 真实配置要求 `writeback.enabled=false`；
- 运行前后校验源 Prompt 未改变；
- 异常信息在输出和失败报告中脱敏。

真实模式可选参数：

```text
--optimizer-provider-name
--optimizer-temperature
--optimizer-max-tokens
--optimizer-think auto|on|off
--max-candidate-proposals
```

## 配置文件

```text
configs/
├── offline.json     # 默认，无 API Key
├── trace.json       # Trace 回放
├── real.json        # 真实业务模型与优化器
└── optimizer.json   # SDK OptimizeConfigFile
```

Pipeline 配置中的路径相对于示例根目录解析，并且不能通过 `..` 或符号链接逃逸该目录。主要字段如下：

| 字段 | 作用 |
|---|---|
| `execution.mode` | `offline`、`trace` 或 `real`。 |
| `execution.candidate_scenario` | 默认候选场景，可由 `--scenario` 覆盖。 |
| `inputs` | train/validation evalset 与优化器配置路径。 |
| `prompts` | 参与快照、候选生成和安全写回的 Prompt 字段。 |
| `run` | run ID、随机种子与产物目录。 |
| `case_labels` | hard/critical Case 标签。 |
| `gate` | 最小验证集提升、退化、关键 Case 和必需 Metric 规则。 |
| `budget` | 成本、Token、耗时与不可观测值策略。 |
| `artifacts` | 输入副本、优化器原生产物和候选审计策略。 |
| `writeback` | Gate ACCEPT 后是否允许写回；示例默认关闭。 |

数据文件集中在：

```text
data/
├── schemas.py       # Pipeline 输入、输出和中间 Pydantic 数据模型
├── config.py        # Pipeline 配置模型与加载
├── evalsets/        # offline/real 的 train 与 validation 数据
└── traces/          # trace baseline/candidate 轨迹和 Prompt 快照
```

## 报告与审计产物

成功运行会原子发布：

```text
runs/<run-id>/report/
├── optimization_report.json
├── optimization_report.md
├── artifact_index.json
├── inputs/
│   ├── pipeline_config.json
│   ├── optimizer_config.json
│   ├── train_evalset.json
│   └── validation_evalset.json
├── evaluations/
│   ├── baseline_train.json
│   ├── baseline_validation.json
│   ├── candidate_train.json
│   └── candidate_validation.json
└── prompts/
    ├── baseline/
    └── candidate/
```

`optimization_report.json` 包含：

- baseline/candidate 的 train 与 validation 评测；
- 逐 Case、逐 Metric 差异；
- 失败归因及其证据；
- Gate 每条规则的结果、拒绝原因和 warning；
- Prompt 写回状态；
- 可观测的耗时、Token、成本和优化器资源信息。

无法可靠观测的数据使用 `unavailable`，不会伪装成零。offline 中不适用的优化器资源使用 `not_applicable`。

`artifact_index.json` 记录每个产物的相对路径、SHA-256、字节数、生产阶段和可用性，用于检查报告发布后是否漂移。

## 失败与排查

如果准备阶段之后发生异常，Pipeline 不会留下看似完整的 `report/`，而是写入：

```text
runs/<run-id>/failure_report.json
```

失败报告包含失败阶段、已经完成的阶段、脱敏错误、Prompt 哈希和已有产物。

常见问题：

### `run directory already exists`

同一个 `run-id` 不允许覆盖。更换 `--run-id`，或检查之前运行的产物。

### `real API calls require explicit --run-real confirmation`

真实配置必须显式传入 `--run-real`，这是费用与外部调用保护，不应关闭。

### `missing required environment variables`

检查 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME` 是否都已设置且非空。

### Gate 返回 REJECT

REJECT 不表示程序异常。打开 `optimization_report.md` 查看拒绝规则，再在 JSON 报告中查看对应 Case Diff 和 Metric 证据。

### `source prompt hash changed`

Pipeline 准备完成后源 Prompt 被其他进程修改。重新开始一个 run，避免把候选写回到已漂移的源版本。

## 代码结构

```text
eval_optimize_loop/
├── agent/             # 业务 Agent、真实模型适配和确定性 Fake Model
├── core/              # Pipeline、评测、优化、Gate、报告与写回
├── data/              # 数据模型、配置模型、evalset 和 trace
├── configs/           # 三种运行模式和优化器配置
├── prompts/           # baseline Prompt
├── sample_output/     # 示例报告
├── run_pipeline.py    # 唯一入口
├── DESIGN.md          # 详细设计与安全边界
└── ROADMAP.md         # 实施阶段记录
```

进一步阅读：[设计说明](DESIGN.md) · [实施路线图](ROADMAP.md)
