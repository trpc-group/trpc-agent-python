# Eval + Optimize Closed-Loop Pipeline

自动化 "评测 → 失败归因 → prompt 优化 → 回归验证 → 产物审计" 闭环。

## 快速开始

```bash
# Fake mode（默认，无需 API Key）
python run_pipeline.py --mode fake

# 详细输出
python run_pipeline.py --mode fake --verbose

# CI 模式（gate 拒绝时 exit 1）
python run_pipeline.py --mode fake --ci

# 自定义优化参数
python run_pipeline.py --mode fake --max-iterations 5 --min-improvement 0.10

# 指定输出目录
python run_pipeline.py --output-dir ./results
```

## 文件结构

```
eval_optimize_loop/
├── run_pipeline.py          # CLI 入口
├── pipeline/                # 7 阶段流水线模块
│   ├── config.py            # 配置加载
│   ├── baseline.py          # 基线评测
│   ├── attribution.py       # 失败归因（8 类）
│   ├── optimize.py          # GEPA 优化
│   ├── validate.py          # 验证集对比
│   ├── gate.py              # 多维度接受决策
│   ├── report.py            # 报告生成
│   └── tracing.py           # 审计追踪
├── agent/                   # 被评测的 Agent
├── data/                    # 评测集 + 配置
└── tests/                   # 129+ 测试
```

## 运行测试

```bash
# 全部测试
python -m pytest tests/ -v

# 按维度运行
python -m pytest tests/test_config.py tests/test_baseline.py tests/test_attribution.py tests/test_gate.py -v
python -m pytest tests/test_pipeline_fake_mode.py tests/test_pipeline_overfit.py -v
python -m pytest tests/test_large_scale.py tests/test_edge_cases.py -v

# 性能报告
python -m pytest tests/ -v --durations=20
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
      "conversation": [{ "..." }],
      "actual_conversation": [{ "...", "intermediate_data": {} }]
    }
  ]
}
```

## 输出

- `sample_output/optimization_report.json` — 机器可读的完整报告
- `sample_output/optimization_report.md` — 人类可读的总结报告

报告包含：baseline 评测结果、失败归因分析、gate 决策（含所有检查项）、验证集对比、
优化器信息、完整审计追踪（seed/timing/cost/reproduce command）。

## CLI 参数

| 参数 | 默认值 | 描述 |
|------|--------|------|
| `--mode` | `fake` | 执行模式：`fake`（零成本）或 `live`（真实 SDK）|
| `--train-evalset` | `data/train.evalset.json` | 训练评测集路径 |
| `--val-evalset` | `data/val.evalset.json` | 验证评测集路径 |
| `--optimizer-config` | `data/optimizer.json` | 优化器配置路径 |
| `--seed` | `42` | 随机种子（确保可复现）|
| `--max-iterations` | `3` | 最大优化迭代轮数 |
| `--min-improvement` | `0.05` | 最小接受提升阈值 |
| `--max-cost` | `10.0` | 优化成本预算（USD）|
| `--output-dir` | `sample_output` | 报告输出目录 |
| `--verbose` / `-v` | `false` | 详细输出 |
| `--ci` | `false` | CI 模式（gate 拒绝时 exit 1）|
