# Eval + Optimize Closed-Loop Pipeline

自动化 **"评测 → 失败归因 → prompt 优化 → 回归验证 → 产物审计"** 闭环示例。
本示例基于 tRPC-Agent 的 `AgentEvaluator` / `AgentOptimizer` 能力，演示如何判断
优化是否真正提升、是否牺牲其他指标、是否过拟合、是否值得回写源 prompt。

## 方案设计说明

**失败归因**：用分层 comparator（数字/短答案/长解释/格式/工具轨迹逐层判定）逐 case 给出 pass/fail 与根因，归因模块按 9 类根因（最终回复不匹配、工具调用/选择/参数错误、rubric 不达标、知识召回不足、格式不符、缺失输出、未知）聚类，每个失败 case 带 detail/evidence/confidence；用黄金判定表锁 ≥90% 归因准确率，避免"只归因不验证"。

**接受策略（gate）**：候选必须在验证集上相对 baseline 逐 case 对比后通过 6 项检查才 ACCEPT——提升达阈值、无负退化、关键 case 不退化、不新增 hard fail、验证集无过拟合回归、优化成本不超预算。阈值全部 CLI 可配，输出 accept/reject/needs_review 三态，`--ci` 映射为退出码 0/1/2。

**防过拟合**：优化后强制用 trace comparator 在验证集重评候选，与 baseline 逐 case 对比（new_pass/new_fail/unchanged）；验证集新增失败 → 拒绝。gate 的 critical-case 保护同时覆盖 train 与 val；overfit 场景对空 val 集/无法扰动的 case 显式报错而非误 ACCEPT。

**产物审计**：每轮候选 prompt、评测结果、gate 决策与理由、运行成本（USD）、分阶段耗时、随机种子、输入文件 sha256 与复现命令全部落盘到 optimization_report.json/.md 与 audit 段；报告含 baseline/candidate/逐 case delta/gate checks，保证优化"可复现、可审计、可回放"。另提供 `tests/test_decision_accuracy.py` 用自有黄金样本验证决策准确率 ≥80%（验收标准 #2）。

## 快速开始

```bash
# Fake mode（默认，无需 API Key，离线可跑）
python run_pipeline.py --mode fake

# 演示"可优化成功"场景（默认）：候选修复失败 → 优化成功 → gate ACCEPT
python run_pipeline.py --mode fake --scenario fix_attributed

# 演示"优化无效"场景：候选无实质改动 → gate NEEDS_REVIEW
python run_pipeline.py --mode fake --scenario noop

# 演示"过拟合退化"场景：train 提升但 val 回归 → gate REJECT
python run_pipeline.py --mode fake --scenario overfit \
  --val-regression-cases val_simple_math_001,val_reasoning_001

# CI 模式（gate 拒绝时 exit 1，需人工审查时 exit 2，可用于自动化回归）
python run_pipeline.py --mode fake --scenario overfit --ci
echo $?   # → 1（REJECT）
python run_pipeline.py --mode fake --scenario noop --ci
echo $?   # → 2（NEEDS_REVIEW）

# Live mode：SDK 接线 + 离线确定性 agent（当前为占位实现，非真实 LLM 端到端优化）
# - baseline 走 SDK AgentEvaluator 的 trace 回放（无需 API key）
# - 优化用 AgentOptimizer，但 reflection_lm 默认 fake，不会调用真实 LLM
# - 验证/门控基于场景模拟候选（报告会显式标注），结果仅作参考
# 配置 TRPC_AGENT_API_KEY 后可用真实模型，未配置时自动降级，不会崩溃
python run_pipeline.py --mode live

# 详细输出
python run_pipeline.py --mode fake --verbose
```

### 三场景快速体验

| 场景 | 命令 | 预期结果 |
|------|------|---------|
| 优化成功 | `python run_pipeline.py --mode fake` | baseline ~71% → candidate ~97%，gate **ACCEPT** |
| 优化无效 | `python run_pipeline.py --mode fake --scenario noop` | candidate = baseline，gate **NEEDS_REVIEW** |
| 过拟合退化 | `python run_pipeline.py --mode fake --scenario overfit --val-regression-cases ...` | train 提升但 val 新增失败，gate **REJECT** |

## 工作原理

1. **评测**：trace 回放评测器（`pipeline/comparator.py`）逐 case 比较
   `conversation`（期望）与 `actual_conversation`（实际回放），判定通过/失败。
2. **失败归因**：将失败 case 聚类到 9 类根因（最终回复不匹配、工具调用错误、
   工具选择错误、参数错误、rubric 不达标、知识召回不足、格式不符、缺失输出、未知）。
3. **优化**：按归因类别生成候选 prompt（模拟 GEPA 反射式变异，离线确定性）。
4. **回归验证**：候选在验证集上逐 case 重评，与 baseline 对比，检测过拟合。
5. **接受决策**：多维 gate（提升阈值、关键 case 保护、新失败检测、过拟合拒绝、成本预算）。
6. **产物审计**：JSON/Markdown 报告 + 完整审计追踪（seed/耗时/成本/复现命令）。

## 文件结构

```
eval_optimize_loop/
├── run_pipeline.py          # 唯一 CLI 入口
├── pipeline/                # 7 阶段流水线
│   ├── __init__.py          # 统一导出（from pipeline import ...）
│   ├── comparator.py        # trace 回放评测器（期望 vs 实际）
│   ├── config.py            # 配置加载（PipelineConfig / evalset / optimizer）
│   ├── baseline.py          # baseline 评测（fake 回放 / SDK）
│   ├── attribution.py       # 失败归因（9 类根因）
│   ├── optimize.py          # 候选生成（三场景）+ AgentOptimizer 集成
│   ├── validate.py          # 候选重评分 + 过拟合检测
│   ├── gate.py              # 多维接受决策
│   ├── report.py            # JSON/Markdown 报告
│   └── tracing.py           # 审计追踪
├── agent/                   # 被优化的目标 Agent + call_agent
│   ├── agent.py             # build_call_agent() / run_agent()
│   ├── config.py            # AgentConfig
│   └── prompts.py           # 基线系统提示词
├── data/                    # 评测集 + 配置
│   ├── train.evalset.json   # 34 cases（含 10 个 _fail 标注）
│   ├── val.evalset.json     # 16 cases（held-out 验证集）
│   ├── large_train.evalset.json  # 50 cases（压力测试）
│   ├── holdout.evalset.json      # 12 cases（hidden 集）
│   ├── optimizer.json       # 优化器配置
│   └── prompts/system.md    # 被优化的系统提示词
├── tests/                   # 300+ 测试（6 维度）
└── sample_output/           # 示例报告输出
```

## 运行测试

```bash
# 全部测试（317 个）
python -m pytest tests/ -q

# 按维度
python -m pytest tests/test_comparator.py tests/test_gold_verdicts.py -q      # 评测器 + 归因锁
python -m pytest tests/test_scenarios.py -q                                  # 三场景端到端
python -m pytest tests/test_attribution_accuracy.py -q                       # 归因准确率
python -m pytest tests/test_live_mode_import.py -q                           # live 健壮性
python -m pytest tests/test_performance.py -q --durations=20                 # 性能

# CI 模式（REJECT → 1，NEEDS_REVIEW → 2）
python run_pipeline.py --mode fake --scenario overfit --ci; echo $?  # → 1
python run_pipeline.py --mode fake --scenario noop --ci; echo $?     # → 2
```

## 配置

### optimizer.json

```json
{
  "evaluate": {
    "metrics": [
      {"metric_name": "final_response_avg_score", "threshold": 0.7},
      {"metric_name": "response_match_score", "threshold": 0.5}
    ]
  },
  "optimize": {
    "algorithm": {
      "name": "gepa_reflective",
      "seed": 42,
      "reflection_lm": {"provider_name": "fake", "model_name": "fake", "api_key": ""},
      "max_metric_calls": 100,
      "timeout_seconds": 600
    }
  }
}
```

### Evalset 格式

```json
{
  "eval_set_id": "my-evalset",
  "eval_cases": [
    {
      "eval_id": "case_001",
      "eval_mode": "trace",
      "conversation": [{
        "user_content": {"parts": [{"text": "问题"}], "role": "user"},
        "final_response": {"parts": [{"text": "期望答案"}], "role": "model"}
      }],
      "actual_conversation": [{
        "user_content": {"parts": [{"text": "问题"}], "role": "user"},
        "final_response": {"parts": [{"text": "实际回放"}], "role": "model"},
        "intermediate_data": {"tool_uses": [], "tool_responses": []}
      }]
    }
  ]
}
```

可选字段：
- `candidate_conversation`：候选优化后的回放内容（隐藏样本场景下按真实回放评分）。
- `intermediate_data.tool_uses` / `tool_responses`：工具轨迹（用于工具层归因）。

## 输出

- `sample_output/optimization_report.json` — 机器可读完整报告
- `sample_output/optimization_report.md` — 人类可读总结报告

报告包含：
- **baseline**：train/validation 通过率、失败 case、指标分解
- **candidate**：候选 train/validation 评分 + 逐 case delta
- **attribution**：失败归因统计 + 每个失败 case 的可解释原因（detail + evidence）
- **gate**：决策（accept/reject/needs_review）+ 理由 + 各检查项明细
- **audit**：seed / 耗时 / 成本 / 复现命令

## CLI 参数

| 参数 | 默认值 | 描述 |
|------|--------|------|
| `--mode` | `fake` | 执行模式：`fake`（零成本离线）或 `live`（SDK 接线 + 离线确定性 agent，验证/门控为模拟）|
| `--scenario` | `fix_attributed` | 候选场景：`fix_attributed` / `noop` / `overfit` |
| `--train-evalset` | `data/train.evalset.json` | 训练评测集路径 |
| `--val-evalset` | `data/val.evalset.json` | 验证评测集路径 |
| `--holdout-evalset` | `data/holdout.evalset.json` | holdout 集路径（可选）|
| `--optimizer-config` | `data/optimizer.json` | 优化器配置路径 |
| `--val-regression-cases` | `` | overfit 场景下要扰动的 val case id（逗号分隔）|
| `--seed` | `42` | 随机种子（确保可复现）|
| `--max-iterations` | `3` | 最大优化迭代轮数 |
| `--min-improvement` | `0.05` | 最小接受提升阈值 |
| `--max-cost` | `10.0` | 优化成本预算（USD）|
| `--output-dir` | `sample_output` | 报告输出目录 |
| `--verbose` / `-v` | `false` | 详细输出 |
| `--ci` | `false` | CI 模式（REJECT → exit 1，NEEDS_REVIEW → exit 2）|
| `--critical-cases` | 空 | 逗号分隔的不可回归关键 case id（train/val 均保护）|

## 验收标准对照

| 验收标准 | 实现方式 |
|---------|---------|
| 6 条样例可运行 + 完整报告 | 4 个 evalset 全可运行，报告含 baseline/candidate/delta/gate |
| 决策准确率 ≥ 80% | SDK 忠实 comparator + 真实候选重评分 |
| 拒绝过拟合候选 | gate 检测验证集新增失败 → REJECT |
| 归因准确率 ≥ 75% | 分层归因 + gold-verdict 回归锁（≥90%）|
| fake/trace ≤ 3 分钟 | 纯 Python 评测，单次 < 3 秒 |
| 报告完整性 | 含 baseline/candidate/逐 case delta/gate/理由 |
