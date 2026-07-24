# Skills Code Review Agent

This example implements Issue #92: a skills-based automatic code review agent with sandbox execution, SQLite persistence, Filter governance, redaction, deduplication and audit reports.

## Quick Start

This example follows the same layout as `examples/quickstart`: keep the runnable
entrypoint at the example root and put agent construction, prompts, config and
tools under `agent/`.

```text
examples/skills_code_review_agent/
├── README.md
├── run_agent.py
├── agent/
│   ├── __init__.py
│   ├── agent.py
│   ├── config.py
│   ├── prompts.py
│   └── tools.py
├── skills/
│   └── code-review/
│       ├── SKILL.md
│       ├── rules/
│       │   └── README.md
│       └── scripts/
│           ├── parse_diff.py
│           └── static_rules.py
├── fixtures/
├── sample_outputs/
└── schema.sql
```

Run one public fixture in dry-run / fake-model mode:

```bash
python examples/skills_code_review_agent/run_agent.py --fixture security_issue --dry-run --output-dir tmp/code_review_security --db-path tmp/code_review_security/review.sqlite3
```

Run all public fixtures:

```bash
python -m pytest tests/examples/test_skills_code_review_agent.py
```

Query the database by task id:

```bash
python examples/skills_code_review_agent/run_agent.py --db-path tmp/code_review_security/review.sqlite3 --query-task-id <task_id>
```

The CLI accepts:

- `--diff-file`: unified diff or PR patch.
- `--repo-path`: local git worktree, reviewed via `git diff`.
- `--path-list-file`: paths to review inside `--repo-path`.
- `--fixture`: fixture name under `fixtures/`.

Outputs are always:

- `review_report.json`
- `review_report.md`
- SQLite rows for task, sandbox runs, filter intercepts, findings, metrics and report.

The deterministic CLI executes the bundled `skills/code-review` scripts directly so it can run without a model key. `agent/tools.py` also exposes `create_review_skill_tool_set()` for wiring the same Skill into a regular `LlmAgent`.
`agent/agent.py` provides that optional `LlmAgent` wrapper, and `agent/config.py` reads the same `TRPC_AGENT_API_KEY`, `TRPC_AGENT_BASE_URL` and `TRPC_AGENT_MODEL_NAME` environment variables used by the quickstart examples. `run_agent.py` remains the acceptance-test entrypoint because it is deterministic and does not need external model credentials.

## Runtime Modes

Production mode is `--runtime container`, which runs skill scripts in a Docker workspace with network disabled and the same `skills/`, `work/` and `out/` layout used by the framework workspace tools. `--dry-run` and `--fake-model` use a deterministic local workspace fallback so the parsing, sandbox, Filter and database chain can be tested without model credentials.

Sandbox execution has a timeout, output byte limit, environment allowlist and secret redaction. Timeouts and failures are recorded as sandbox runs and manual-review items; they do not crash the review.

## Public Fixtures

- `no_issue`: benign change with tests.
- `security_issue`: `shell=True` and `eval`.
- `async_resource_leak`: unscoped `aiohttp.ClientSession` and unobserved background task.
- `db_lifecycle_issue`: unscoped DB connection and string-built SQL.
- `missing_tests`: production change without tests.
- `duplicate_finding`: repeated secret finding used to verify deduplication.
- `sandbox_failure`: used by tests to force script failure while keeping the task alive.
- `secret_redaction`: API key, token, password and bearer credential redaction.

## 300-500 字方案设计

本示例以 `examples/skills_code_review_agent` 形式交付，避免改动核心 SDK。`code-review` Skill 包含 `SKILL.md`、规则文档和两个脚本：`parse_diff.py` 负责抽取文件、hunk 与增删行统计，`static_rules.py` 负责在沙箱中产出高信号静态检查结果。Agent 入口支持 `--diff-file`、`--repo-path`、`--path-list-file` 和 `--fixture`，先解析 unified diff 得到变更文件、候选行号和上下文，再合并沙箱脚本与内置规则结果。dry-run / fake-model 模式不依赖真实模型 API Key，保证公开样本和 CI 中的链路可重复。

沙箱由 `SandboxRunner` 封装。生产默认使用 `--runtime container`，Docker 运行时禁用网络；dry-run 只作为开发 fallback，在临时 workspace 中执行同一套 Skill 脚本。每个沙箱请求都先经过 `ReviewExecutionFilter`：禁止敏感路径、路径穿越、非白名单网络访问、超时或输出超预算请求；对 curl 管道、包安装、破坏性命令和提权命令标记 `needs_human_review`，并直接写入报告和数据库，不能继续执行。

SQLite 默认 schema 包含 `review_task`、`sandbox_run`、`finding`、`filter_intercept`、`review_metric` 和 `review_report`，可通过 task id 查询完整任务、执行摘要、拦截记录、监控指标、findings 与最终报告。存储实现集中在 `ReviewStore`，后续可替换为其他 SQL 后端。去重以 `(file, line, category)` 为键，保留置信度和严重级别更高的结果；低置信度或测试缺失等弱信号进入 warnings / needs_human_review。脱敏覆盖输入 diff、沙箱 stdout/stderr、产物、findings、Markdown/JSON 报告和数据库行，避免 API Key、token、password、私钥等明文落盘。最终报告包含 findings 摘要、严重级别统计、人工复核项、Filter 拦截摘要、监控指标、沙箱执行摘要和可执行修复建议。
