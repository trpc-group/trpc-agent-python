# Code Review Agent / 代码评审 Agent

基于 tRPC-Agent Skills + 沙箱 + 数据库的自动化代码评审 Agent。
Automated code review agent built on tRPC-Agent Skills + sandbox + database storage.

## 功能概述 / Features

读取 git diff → 安全检查（Filter）→ 规则扫描（10 类）→ 沙箱执行 → 去重脱敏 → 结构化报告 → 数据库存储。
Read git diff → safety filter → scan (10 categories) → sandbox execution → dedup + redact → structured report → persist to DB.

### 核心能力 / Key Capabilities

- **10 个扫描器**：安全、异步错误、资源泄漏、DB 生命周期、缺失测试、密钥泄露、裸 except、可变默认参数、assert 控制流、硬编码路径
- **三级置信度**：high (≥0.8) / warning (≥0.55) / needs_human_review (<0.55)
- **Policy-as-Code 过滤器**：外部化安全策略，支持命令阻止和网络控制
- **多运行时沙箱**：Fake（CI/测试）/ Local（开发）/ Workspace（生产）
- **AST 污点分析**：Python AST + JS/TS 正则分析
- **Schema 版本化**：增量列迁移，向前兼容
- **Multi-Format Output**: JSON + Markdown + SARIF (GitHub Code Scanning)
- **Fixture Evaluation**: Precision/recall/F1 with cross-validation

## 快速开始 / Quick Start

```bash
# 安装依赖 / Install dependencies
pip install -e ".[gepa]"

# 运行单个 diff 评审 / Run review on a diff file
python run_review.py --diff-file fixtures/diffs/security.diff

# Dry-run 模式（无需 API Key）/ Dry-run mode
python run_review.py --diff-file fixtures/diffs/security.diff --dry-run

# 从 stdin 读取 diff / Read diff from stdin
git diff | python run_review.py --diff-file -

# 增量 review（仅新 commit）/ Incremental review
python run_review.py --repo-path . --since-commit HEAD~1

# 运行全部测试 / Run all tests
python -m pytest tests/ -v

# Fixture 评估 / Evaluate fixture accuracy
python evaluate_fixtures.py --fixtures fixtures/diffs/ --expected fixtures/expected_findings.json
```

## CLI 参数 / CLI Options

| 参数 | 说明 | Description |
|------|------|-------------|
| `--diff-file PATH` | Diff 文件路径（`-` = stdin） | Path to diff file |
| `--repo-path PATH` | Git 仓库路径 | Path to git repo |
| `--output-dir DIR` | 报告输出目录 | Report output directory |
| `--db-path PATH` | SQLite 数据库路径 | SQLite DB path |
| `--dry-run` | Fake 模式，无需 LLM | No LLM calls |
| `--verbose, -v` | 详细输出 | Verbose output |

## 扫描规则 / Scan Categories

| 类别 / Category | 说明 / Description | 示例 / Example |
|------|------|------|
| Security | 命令注入、不安全反序列化、动态导入 | `os.system()`, `eval()`, `pickle.loads()` |
| Async Error | 事件循环阻塞、缺少 await | `time.sleep()` in async |
| Resource Leak | 文件句柄未关闭、连接泄漏 | `open()` without context manager |
| DB Lifecycle | 游标/连接未关闭、事务未提交 | `cursor.execute()` without commit |
| Missing Tests | 新函数无对应测试 | `def new_func()` without `test_new_func` |
| Secret Info | 硬编码密钥/密码/Token | API keys, GitHub tokens, AWS keys |
| Bare Except | 裸 except 子句 | `except:` catches KeyboardInterrupt |
| Mutable Defaults | 可变默认参数 | `def f(x=[])` shared across calls |
| Assert Flow | assert 用于控制流 | `assert` stripped with `-O` flag |
| Hardcoded Paths | 硬编码绝对路径 | `/home/user/file.txt`, `C:\path\file` |

## 目录结构 / Directory Structure

```
examples/skills_code_review_agent/
├── run_review.py            # CLI entry point
├── evaluate_fixtures.py     # Precision/recall/F1 evaluation
├── DESIGN.md                # Architecture design doc
├── README.md                # This file
├── ai-prompts.md            # Development process record
├── pipeline/                # Review pipeline
│   ├── types.py             # Data contracts
│   ├── config.py            # Configuration
│   ├── diff_parser.py       # Unified diff parser
│   ├── filter_chain.py      # Safety filter chain
│   ├── scanners.py          # 10 pattern-matching scanners
│   ├── sandbox.py           # Fake/Local/Workspace sandbox
│   ├── dedup.py             # Dedup + 3-tier confidence
│   ├── redaction.py         # Secret redaction (12 patterns)
│   ├── ast_analyzer.py      # Python AST + JS/TS taint analysis
│   ├── report.py            # JSON + Markdown reports
│   ├── sarif_output.py      # SARIF v2.1.0 output
│   └── telemetry.py         # Monitoring / audit
├── storage/                 # SQLite persistence
│   ├── schema.sql           # Database schema
│   ├── models.py            # Data models
│   └── dao.py               # Data access layer (schema versioning)
├── agent/                   # tRPC-Agent integration
│   ├── agent.py             # LlmAgent wrapper
│   ├── config.py            # Model config
│   └── prompts.py           # System prompt
├── fixtures/                # Test fixtures
│   ├── diffs/               # 8 diff fixtures
│   └── expected_findings.json # Labeled findings
├── tests/                   # 205 tests (15 files)
│   ├── test_scanners.py     # 28 tests
│   ├── test_edge_cases.py   # 22 tests
│   ├── test_ast_analyzer.py # 18 tests
│   ├── test_redaction.py    # 13 tests
│   ├── test_cli.py          # 12 tests
│   ├── test_storage.py      # 12 tests
│   ├── test_pipeline_fake_mode.py # 12 tests
│   ├── test_diff_parser.py  # 11 tests
│   ├── test_dedup.py        # 11 tests
│   ├── test_report.py       # 11 tests
│   ├── test_filter_chain.py # 10 tests
│   ├── test_fixture_evaluation.py # 10 tests
│   ├── test_performance.py  # 8 tests
│   ├── test_sandbox.py      # 8 tests
│   ├── test_agent.py        # 8 tests (mocked)
│   └── conftest.py          # Shared fixtures
└── skills/code-review/      # Code-review Skill
    ├── SKILL.md
    ├── rules/rules.json
    ├── filter_policy.json
    ├── docs/OUTPUT_SCHEMA.md
    └── scripts/
        ├── run_checks.py
        └── parse_diff.py
```

## 输出格式 / Output Formats

### JSON (`review_report.json`)
```json
{
  "task_id": "review-20260708-...",
  "summary": {
    "total_findings": 6,
    "high_confidence": 4,
    "warning": 1,
    "needs_human_review": 1,
    "by_severity": {"critical": 2, "high": 2, "medium": 1, "low": 1}
  },
  "findings": [...],
  "filter_summary": {...},
  "sandbox_summary": {...},
  "telemetry": {...},
  "recommendations": [...]
}
```

### Markdown (`review_report.md`)
- Findings 摘要和严重级别统计 / Severity summary
- 三级置信度分布 / 3-tier confidence breakdown
- 按严重级别排序的发现列表 / Severity-sorted findings
- 人工复核项 / Human review items
- Filter 拦截摘要 / Filter decision summary
- 沙箱执行摘要 / Sandbox execution summary
- 可执行修复建议 / Actionable recommendations

### SARIF (`review_report.sarif.json`)
- GitHub Code Scanning 集成 / GitHub Code Scanning integration
- Azure DevOps 兼容 / Azure DevOps compatible
- SARIF v2.1.0 标准 / SARIF v2.1.0 compliant

## 数据库 / Database

SQLite 存储，4 张表 + schema 版本化：
SQLite with 4 tables + schema versioning:

- `schema_version` — 数据库 schema 版本追踪 / Tracks DB schema version
- `review_tasks` — 评审任务 / Review tasks
- `findings` — 发现的问题（含置信度分级）/ Findings with confidence tiers
- `sandbox_runs` — 沙箱执行记录 / Sandbox execution records
- `filter_logs` — Filter 拦截日志 / Filter decision audit trail

Schema 版本化支持增量列迁移（v1→v4），向前兼容。
Schema versioning with incremental column migrations (v1→v4).

## 测试 / Tests

205 tests across 15 files, covering:
- **单元测试 / Unit**: Each pipeline module independently
- **集成测试 / Integration**: Full 8-stage pipeline end-to-end
- **边界测试 / Edge Cases**: Empty diffs, Unicode/emoji/multi-language, oversized inputs
- **性能测试 / Performance**: Diff scaling, dedup performance, report generation
- **Fixture 评估 / Evaluation**: Precision/recall/F1 per fixture with cross-validation

```bash
# Run all tests
python -m pytest tests/ -v

# Run with timing report
python -m pytest tests/ -v --durations=20

# Run specific test file
python -m pytest tests/test_scanners.py -v
```
