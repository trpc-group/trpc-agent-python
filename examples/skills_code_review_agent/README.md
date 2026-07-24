# Skills Code Review Agent

Automated code review agent powered by the tRPC-Agent [Skills](https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent/blob/master/docs/skills/skills_zh_CN.md) framework. It demonstrates end-to-end sandboxed execution of review checkers (security, async/resource leaks, DB lifecycle, missing tests, secret leaks), governance via tool-level Filter, SQL-based persistence of findings + metrics + reports, and host-side redaction. The example runs deterministic checker scripts inside a container/workspace sandbox and optionally enriches results with an LLM pass.

```
   Unified diff (file / repo / fixture)
              |
              v
      +-- Parse diff --+
      |  (parse_diff)  |
      +----------------+
              |
              v
   +--- Governance Filter ----+
   | script allowlist, paths, |
   | network deny, budget,    |
   | risk → needs_human_review|
   +--------------------------+
              |
              v
   +---- Sandbox (container / local / cube) ----+
   | env -i (PATH+HOME+LANG whitelist)           |
   | security | async_leak | db_lifecycle         |
   | tests_missing | secrets                       |
   | timeout: 60s, output cap: 256KB               |
   +-----------------------------------------------+
              |
              v
   +--- LLM enrichment (optional) ---+
   | confidence boost, false-positive |
   | suppression, prose summary      |
   +---------------------------------+
              |
              v
   +--- Dedup + Gating ---+
   | file:line:category    |
   | confidence >= 0.6     |
   +-----------------------+
              |
              v
   +---- Persist to SQLite ----+
   | 6 tables, task-id indexed |
   +---------------------------+
              |
              v
   +-- Redaction + Reports --+
   | review_report.json/.md  |
   +-------------------------+
```

## Quick Start

```bash
cd examples/skills_code_review_agent

# Dev-fallback runtime, no API key needed:
python run_agent.py review --fixture security_eval --runtime local --dry-run

# Production default (requires Docker):
python run_agent.py review --diff-file my_change.diff

# Query a stored review:
python run_agent.py show --task-id <id>
```

## CLI Reference

| Flag | Values | Default | Description |
|---|---|---|---|
| `--diff-file` | path | — | Path to a unified diff / PR patch |
| `--repo-path` | path | — | Git repo; reviews `git diff HEAD` |
| `--fixture` | name | — | Bundled fixture (e.g. `security_eval`) |
| `--runtime` | `local` / `container` / `cube` | `container` | Sandbox runtime; `local` is dev-only |
| `--dry-run` | flag | off | Use deterministic fake model, no API key |
| `--db-url` | URL | `sqlite:///code_review.db` | SQLite path or DB URL |
| `--output-dir` | path | `out` | Directory for JSON + Markdown reports |
| `--no-llm` | flag | off | Skip the LLM enrichment step |

## Rule Categories

| Category | Script | Example Patterns |
|---|---|---|
| `security` | `check_security.py` | `eval`, `exec`, `shell=True`, `pickle`, `yaml.load`, SQL injection |
| `async_resource_leak` | `check_async_leak.py` | Unreferenced asyncio tasks, unmanaged sessions/files |
| `db_lifecycle` | `check_db_lifecycle.py` | DB connection/cursor/transaction lifecycle issues |
| `missing_test` | `check_tests_missing.py` | Source files changed without corresponding test changes |
| `secret_leak` | `check_secrets.py` | Hardcoded API keys, tokens, passwords (evidence pre-redacted) |

## Database Schema

| Table | Key Columns |
|---|---|
| `cr_review_tasks` | `id`, `status`, `input_type`, `input_ref`, `runtime`, `dry_run`, `diff_summary`, `created_at`, `finished_at` |
| `cr_sandbox_runs` | `id`, `task_id`, `script`, `category`, `status`, `exit_code`, `duration_ms`, `timed_out`, `stdout_summary`, `stderr_summary`, `error_type` |
| `cr_findings` | `id`, `task_id`, `severity`, `category`, `file`, `line`, `title`, `evidence`, `recommendation`, `confidence`, `source`, `status`, `dedup_key` |
| `cr_filter_events` | `id`, `task_id`, `target`, `decision`, `rule`, `reason`, `created_at` |
| `cr_metrics` | `id`, `task_id`, `total_duration_ms`, `sandbox_duration_ms`, `tool_calls`, `intercepts`, `findings_total`, `severity_distribution`, `error_distribution` |
| `cr_reports` | `id`, `task_id`, `report_json`, `report_md`, `created_at` |

All tables are joinable via `task_id` (foreign key to `cr_review_tasks.id`).

## Security Boundaries

- **Environment isolation**: Every checker script runs under `env -i` with only `PATH`, `HOME`, and `LANG` in the environment. No host env vars leak into the sandbox.
- **Timeouts**: Each script execution has a 60-second timeout (configurable). Total sandbox budget is capped at 300 seconds / 20 runs.
- **Output caps**: stdout and stderr are truncated at 256 KB per run.
- **Redaction**: Host-side regex-based redaction (shared pattern library with checkers) is applied to all reports and DB-stored content. No plaintext secrets survive in reports or the database.
- **Filter governance**: The `GovernanceToolFilter` blocks LLM-initiated tool calls outside the script allowlist, denies network tools (`curl`, `wget`, `pip`, `git`, etc.), forbids absolute/relative path escapes, and escalates high-risk commands (`sudo`, `docker`, `rm`, etc.) to `needs_human_review`.
- **Local runtime**: `--runtime local` is a development-only shortcut. Production deployments must use `container` or `cube`.

## Testing

```bash
# Run all tests (container tests are skipped by default):
python -m pytest examples/skills_code_review_agent/tests -q

# Opt into container-based tests (requires Docker):
CR_CONTAINER_TESTS=1 python -m pytest examples/skills_code_review_agent/tests -q
```

## 方案设计说明

**Skill 设计**：采用 SKILL.md 声明的规则文档 + 脚本架构，每个 checker 脚本以 JSON 契约输出 `{"findings": [...]}`，包含 severity、category、file、line、evidence、recommendation、confidence、source 字段，确保静态分析与 LLM 富化结果统一格式。

**沙箱隔离策略**：生产默认使用 container 运行时创建独立工作空间，`env -i` 启动脚本仅注入 PATH/HOME/LANG 白名单，单次超时 60 秒，预算上限 300 秒 / 20 次运行，输出截断 256 KB。local 运行时仅用于开发调试。

**Filter 策略**：GovernanceEngine 实施脚本白名单（6 个 checker），禁止网络工具（curl、wget、pip、git 等），拦截绝对路径 / `..` 穿越，高危命令（sudo、docker、rm 等）进入 `needs_human_review` 人工复核，超出预算直接 deny。

**监控字段**：`cr_metrics` 表记录 total_duration_ms、sandbox_duration_ms、tool_calls、intercepts、findings_total、severity_distribution（JSON 分布）和 error_distribution，可按 task_id 追溯全链路性能与异常。

**数据库 schema**：六表设计 — `cr_review_tasks`（任务），`cr_sandbox_runs`（沙箱运行记录），`cr_findings`（发现项），`cr_filter_events`（拦截事件），`cr_metrics`（监控指标），`cr_reports`（报告），均通过 task_id 外键关联查询。

**去重降噪**：file + line + category 三维联合去重，相同键保留最高 severity 与 confidence，置信度 < 0.6 的发现进入 `needs_human_review` 人工确认，避免低质量误报淹没关键问题。

**安全边界**：全链路脱敏 — 报告写入前经 host 侧 redaction（与 checker 共享 secret_patterns.py 规则），数据库存储同样经过脱敏，确保报告文件与 SQLite 库中绝无明文密钥泄露。整个方案通过技能化、沙箱化、治理化和持久化四个维度实现生产级代码审查自动化。
