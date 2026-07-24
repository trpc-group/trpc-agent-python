# Skills 代码评审 Agent

本示例展示如何把可复用 Skill、沙箱执行、Filter 治理、SQLite 持久化、结构化 finding 和监控审计串成一个自动代码评审 Agent。

## 覆盖能力

- 提供 `skills/code-review/SKILL.md`、规则文档和沙箱脚本。
- 支持 unified diff、PR patch、本地 git 工作区、文件列表、stdin 和 fixture 输入。
- CLI 和 `run_review()` API 默认生产沙箱为 `container`；显式 `--dry-run` 或 `--sandbox fake` 使用 fake sandbox，适合 CI 和无模型 API Key 的本地开发。
- 支持 Container/Cube workspace runtime，`local` 只作为显式开发 fallback。
- SQLite 记录任务、输入摘要、沙箱执行、Filter 决策、finding、监控摘要和最终报告。
- 报告和数据库写入前执行敏感信息脱敏。
- 普通评审不会在沙箱策略外执行 SDK `skill_run`；`--skill-smoke` 可单独验证 SDK `skill_load` / `skill_run` 链路。
- Finding 去重，并按置信度分流到 `warnings` 和 `needs_human_review`。
- 规则覆盖安全风险、异步错误、资源泄漏、测试缺失、敏感信息泄漏、数据库事务、数据库连接生命周期七类问题，并包含 Python AST/taint 辅助分析。
- 沙箱侧默认聚合离线 bandit、ruff、detect-secrets；网络型 semgrep auto、测试命令中的 URL/domain，以及 pip/git/npm/ssh 等隐式联网命令需要先经过 Filter。
- 沙箱输出按 stdout/stderr 合计字节预算截断；scanner 物化 diff 文件时拒绝路径逃逸。
- `rules.json` 和 `filter_policy.json` 提供规则配置、Filter policy-as-code 和 workspace 路径 allowlist。
- 支持 `# cr-agent: ignore=<rule_id>` 忽略特定规则并记录 ignored count。
- 提供 `agent/native_agent.py`，可把完整评审流水线作为 `FunctionTool` 挂载到 `LlmAgent`。
- 提供 `agent/native_filter.py`，可把同一套治理策略作为 tRPC-Agent `BaseFilter` adapter 复用。
- `ReviewStore.from_url()` 支持 `sqlite:///...`，并为 Postgres/MySQL 保留清晰 SQL 后端扩展点。
- 报告包含置信度阈值、去重数量、Filter 摘要、沙箱摘要、监控指标和可执行修复建议。

## 运行方式

在仓库根目录执行：

```bash
python3 examples/skills_code_review_agent/run_review.py --fixture security_issue --dry-run
```

运行公开样例：

```bash
for f in clean security_issue async_resource_leak db_lifecycle missing_tests duplicate_findings sandbox_failure secret_redaction sandbox_timeout sandbox_large_output sandbox_secret_output; do
  python3 examples/skills_code_review_agent/run_review.py --fixture "$f" --dry-run --output-dir /tmp/cr-agent-"$f"
done
```

验证 SDK 原生 Skill 链路。该命令会真实调用 `skill_load(skill_name="code-review")`，再通过
`skill_run` 执行 Skill 目录下的 `scripts/diff_summary.py`。这是独立 smoke check；普通审查流水线不会在 Filter
和所选沙箱策略外执行 SDK `skill_run`：

```bash
python3 examples/skills_code_review_agent/run_review.py --skill-smoke
```

审查 diff 文件：

```bash
python3 examples/skills_code_review_agent/run_review.py --diff-file /path/to/change.diff --dry-run
```

从 stdin 或 PR patch 文件读取：

```bash
git diff | python3 examples/skills_code_review_agent/run_review.py --diff-file - --dry-run
python3 examples/skills_code_review_agent/run_review.py --patch-file /path/to/pr.patch --dry-run
```

审查本地 git 工作区：

```bash
python3 examples/skills_code_review_agent/run_review.py --repo-path /path/to/repo --dry-run
```

审查文件路径列表：

```bash
printf "app/service.py\napp/repository.py\n" > /tmp/changed-files.txt
python3 examples/skills_code_review_agent/run_review.py --file-list /tmp/changed-files.txt --dry-run
```

在通过 Filter 的沙箱请求中执行单元测试命令：

```bash
python3 examples/skills_code_review_agent/run_review.py --fixture clean --dry-run --test-command "python3 -m pytest -q tests/examples/test_skills_code_review_agent.py"
```

执行 Skill 下通过 Filter 的自定义规则脚本：

```bash
python3 examples/skills_code_review_agent/run_review.py \
  --fixture security_issue \
  --dry-run \
  --custom-rule-script scripts/static_review.py
```

验证网络 scanner 拦截：

```bash
python3 examples/skills_code_review_agent/run_review.py \
  --fixture clean \
  --dry-run \
  --include-network-scanners
```

验证 Filter 超预算拦截：

```bash
python3 examples/skills_code_review_agent/run_review.py \
  --fixture clean \
  --dry-run \
  --timeout-sec 10 \
  --filter-timeout-budget-sec 1
```

运行 fixture 回归评测：

```bash
python3 examples/skills_code_review_agent/evaluate_fixtures.py --json
```

作为 tRPC-Agent 工具使用：

```python
from examples.skills_code_review_agent.agent.native_agent import create_code_review_agent
from trpc_agent_sdk.code_executors import create_container_workspace_runtime

runtime = create_container_workspace_runtime()
agent = create_code_review_agent(model=my_model, skill_workspace_runtime=runtime)
```

按 task id 查询数据库记录：

```bash
python3 examples/skills_code_review_agent/run_review.py --task-id cr_xxxxx --db-path examples/skills_code_review_agent/sample_outputs/review_tasks.sqlite
```

使用 SQLite URL：

```bash
python3 examples/skills_code_review_agent/run_review.py \
  --fixture security_issue \
  --dry-run \
  --db-url sqlite:////tmp/review_tasks.sqlite
```

## 输出

每次运行会写入：

- `review_report.json`
- `review_report.md`
- `review_tasks.sqlite`

报告包含 finding 摘要、严重级别统计、warnings、人工复核项、Filter 决策和拦截摘要、沙箱执行摘要、监控指标和可执行修复建议。

## 沙箱模式

- `container`：CLI 和 `run_review()` API 默认生产沙箱，Docker backed workspace runtime，作为本地可验证的生产沙箱路径。
- `cube`：Cube/E2B workspace runtime，可对接 E2B-compatible CubeSandbox 服务。
- `fake`：显式 dry-run 模式，确定性、可离线、适合 CI。
- `local`：仅用于显式开发 fallback。

Docker 可用时可以运行 Container 模式：

```bash
python3 examples/skills_code_review_agent/run_review.py \
  --fixture security_issue \
  --sandbox container \
  --container-image python:3-slim \
  --test-command "python3 -m pytest -q"
```

默认 `python:3-slim` 是可直接拉取的生产沙箱基线，用来验证 Container workspace、隔离、超时和输出限制链路；
它不内置 `bandit`、`ruff`、`detect-secrets`。如需在容器中实际运行这些离线 scanner，可先构建示例镜像：

```bash
docker build -f examples/skills_code_review_agent/Dockerfile.scanners \
  -t trpc-agent-code-review-scanners examples/skills_code_review_agent

python3 examples/skills_code_review_agent/run_review.py \
  --fixture security_issue \
  --sandbox container \
  --container-image trpc-agent-code-review-scanners
```

设置 `CR_AGENT_RUN_DOCKER_SMOKE=1` 且 Docker daemon 可用时，可显式执行 Container smoke：

```bash
CR_AGENT_RUN_DOCKER_SMOKE=1 python3 -m pytest -q tests/examples/test_skills_code_review_agent.py::test_container_runtime_smoke_executes_skill_script
```

Cube 模式需要安装可选依赖并提供 Cube/E2B 配置：

```bash
python3 examples/skills_code_review_agent/run_review.py \
  --fixture security_issue \
  --sandbox cube \
  --cube-template "$CUBE_TEMPLATE_ID" \
  --cube-api-url "$E2B_API_URL" \
  --cube-api-key "$E2B_API_KEY"
```

## 公开 Fixture

- `clean`：无问题 diff，包含测试更新。
- `security_issue`：shell 执行、动态 eval、关闭 TLS 校验。
- `async_resource_leak`：未跟踪异步任务、未关闭 session/file。
- `db_lifecycle`：连接/session 生命周期、事务处理和 SQL 插值。
- `missing_tests`：源码变更缺少测试变更。
- `duplicate_findings`：重复问题模式，用于去重测试。
- `sandbox_failure`：沙箱失败被记录，任务不崩溃。
- `secret_redaction`：报告和数据库中脱敏 secret。
- `sandbox_timeout`：沙箱超时被记录，任务不崩溃。
- `sandbox_large_output`：沙箱输出被截断并记录。
- `sandbox_secret_output`：沙箱 stdout/stderr 中的 secret 被脱敏。
- `hidden_like_multiline`：多行 shell=True、SQL 变量拼接后 execute、保存但未观察的 task。
- `ast_taint`：函数参数/request taint 流向 shell 和 SQL sink。
- `ignore_rule`：`# cr-agent: ignore=<rule_id>` 忽略规则样本。
- `entropy_secret`：高熵字面量 secret 脱敏样本。
- `external_scanner`：外部 scanner finding 合并入报告和数据库的样本。
- `resource_lifecycle_closed`：有 finally close 的资源生命周期样本，用于降误报。
- `secret_redaction_extended`：AWS、Slack、JWT 和通用 token 脱敏样本。

# Skills Code Review Agent

This example demonstrates an automatic code review agent built from a reusable Skill, sandbox execution, Filter governance, SQLite persistence, structured findings, and monitoring audit output.

## What It Covers

- `skills/code-review/SKILL.md` with rule docs and sandbox scripts.
- Unified diff, PR patch, repo path, file-list, stdin, and fixture input parsing.
- CLI and `run_review()` default to the production `container` sandbox; explicit `--dry-run` or `--sandbox fake` uses the fake sandbox for CI and no-key development.
- Container/Cube-ready sandbox adapter boundary, with local mode only as explicit fallback.
- SQLite records for task, diff, sandbox run, filter decision, finding, monitoring summary, and final report.
- Secret redaction before report and database writes.
- Normal reviews do not execute SDK `skill_run` outside the sandbox policy; `--skill-smoke` verifies SDK `skill_load` / `skill_run` separately.
- Deduped findings plus warnings and `needs_human_review`.
- Review rules cover seven categories: security risks, async errors, resource leaks, test gaps, secret leaks, database transactions, and database connection lifecycle, with Python AST/taint assistance.
- Sandbox-side scanner aggregation runs offline bandit, ruff, and detect-secrets by default; network-backed semgrep auto scanning, URLs/domains in test commands, and implicit network commands such as pip/git/npm/ssh must pass Filter.
- Sandbox output uses a combined stdout/stderr byte budget; scanner materialization rejects path escapes.
- `rules.json` and `filter_policy.json` provide rule configuration, Filter policy-as-code, and workspace path allowlists.
- `# cr-agent: ignore=<rule_id>` suppresses a specific rule and records ignored counts.
- `agent/native_agent.py` exposes the complete review pipeline as a `FunctionTool` for `LlmAgent`.
- `agent/native_filter.py` exposes the same governance policy as a tRPC-Agent `BaseFilter` adapter.
- `ReviewStore.from_url()` supports `sqlite:///...` and keeps explicit Postgres/MySQL extension points.
- Reports include confidence thresholds, dedupe counts, Filter summaries, sandbox summaries, monitoring metrics, and actionable recommendations.

## Run

From the repository root:

```bash
python3 examples/skills_code_review_agent/run_review.py --fixture security_issue --dry-run
```

Run all public fixtures:

```bash
for f in clean security_issue async_resource_leak db_lifecycle missing_tests duplicate_findings sandbox_failure secret_redaction sandbox_timeout sandbox_large_output sandbox_secret_output; do
  python3 examples/skills_code_review_agent/run_review.py --fixture "$f" --dry-run --output-dir /tmp/cr-agent-"$f"
done
```

Verify the SDK-native Skill path. This calls `skill_load(skill_name="code-review")`
and then runs `scripts/diff_summary.py` through `skill_run`. This is a standalone smoke check;
normal reviews do not execute SDK `skill_run` outside the Filter and selected sandbox policy:

```bash
python3 examples/skills_code_review_agent/run_review.py --skill-smoke
```

Review a diff file:

```bash
python3 examples/skills_code_review_agent/run_review.py --diff-file /path/to/change.diff --dry-run
```

Read a diff from stdin or a PR patch file:

```bash
git diff | python3 examples/skills_code_review_agent/run_review.py --diff-file - --dry-run
python3 examples/skills_code_review_agent/run_review.py --patch-file /path/to/pr.patch --dry-run
```

Review a local git working tree:

```bash
python3 examples/skills_code_review_agent/run_review.py --repo-path /path/to/repo --dry-run
```

Review a file list:

```bash
printf "app/service.py\napp/repository.py\n" > /tmp/changed-files.txt
python3 examples/skills_code_review_agent/run_review.py --file-list /tmp/changed-files.txt --dry-run
```

Run a configured unit test command inside an approved sandbox request:

```bash
python3 examples/skills_code_review_agent/run_review.py --fixture clean --dry-run --test-command "python3 -m pytest -q tests/examples/test_skills_code_review_agent.py"
```

Run a custom rule script from the Skill after Filter approval:

```bash
python3 examples/skills_code_review_agent/run_review.py \
  --fixture security_issue \
  --dry-run \
  --custom-rule-script scripts/static_review.py
```

Verify Filter budget interception:

```bash
python3 examples/skills_code_review_agent/run_review.py \
  --fixture clean \
  --dry-run \
  --timeout-sec 10 \
  --filter-timeout-budget-sec 1
```

Run fixture regression evaluation:

```bash
python3 examples/skills_code_review_agent/evaluate_fixtures.py --json
```

Use as a tRPC-Agent tool:

```python
from examples.skills_code_review_agent.agent.native_agent import create_code_review_agent
from trpc_agent_sdk.code_executors import create_container_workspace_runtime

runtime = create_container_workspace_runtime()
agent = create_code_review_agent(model=my_model, skill_workspace_runtime=runtime)
```

Query stored task data:

```bash
python3 examples/skills_code_review_agent/run_review.py --task-id cr_xxxxx --db-path examples/skills_code_review_agent/sample_outputs/review_tasks.sqlite
```

Use a SQLite URL:

```bash
python3 examples/skills_code_review_agent/run_review.py \
  --fixture security_issue \
  --dry-run \
  --db-url sqlite:////tmp/review_tasks.sqlite
```

## Outputs

Each run writes:

- `review_report.json`
- `review_report.md`
- `review_tasks.sqlite`

The report includes findings, severity statistics, warnings, human-review items, Filter decisions and interception summaries, sandbox summaries, monitoring metrics, and actionable recommendations.

## Sandbox Modes

- `container`: CLI and `run_review()` default production target for Docker-backed workspace execution.
- `cube`: production target for Cube/E2B workspace execution.
- `fake`: explicit dry-run mode, deterministic and CI-safe.
- `local`: explicit development fallback only.

Container mode can be invoked when Docker is available:

```bash
python3 examples/skills_code_review_agent/run_review.py \
  --fixture security_issue \
  --sandbox container \
  --container-image python:3-slim \
  --test-command "python3 -m pytest -q"
```

The default `python:3-slim` image is a directly pullable production sandbox baseline
for verifying the Container workspace path, isolation, timeouts, and output caps; it
does not include `bandit`, `ruff`, or `detect-secrets`. Build the example scanner
image when you want those offline scanners to run inside the container:

```bash
docker build -f examples/skills_code_review_agent/Dockerfile.scanners \
  -t trpc-agent-code-review-scanners examples/skills_code_review_agent

python3 examples/skills_code_review_agent/run_review.py \
  --fixture security_issue \
  --sandbox container \
  --container-image trpc-agent-code-review-scanners
```

Set `CR_AGENT_RUN_DOCKER_SMOKE=1` with Docker reachable to run the Container smoke explicitly:

```bash
CR_AGENT_RUN_DOCKER_SMOKE=1 python3 -m pytest -q tests/examples/test_skills_code_review_agent.py::test_container_runtime_smoke_executes_skill_script
```

Cube mode requires the optional Cube/E2B dependency and credentials:

```bash
python3 examples/skills_code_review_agent/run_review.py \
  --fixture security_issue \
  --sandbox cube \
  --cube-template "$CUBE_TEMPLATE_ID" \
  --cube-api-url "$E2B_API_URL" \
  --cube-api-key "$E2B_API_KEY"
```

## Public Fixtures

- `clean`: no issue with matching test update.
- `security_issue`: shell execution, dynamic eval, disabled TLS verification.
- `async_resource_leak`: untracked task plus unclosed sessions/files.
- `db_lifecycle`: connection/session lifecycle, transaction handling, and SQL interpolation.
- `missing_tests`: source change without test change.
- `duplicate_findings`: repeated issue pattern used with unit dedupe checks.
- `sandbox_failure`: sandbox failure recorded without crashing the task.
- `secret_redaction`: secrets are redacted in reports and database rows.
- `sandbox_timeout`: sandbox timeout recorded without crashing the task.
- `sandbox_large_output`: sandbox output truncation is recorded.
- `sandbox_secret_output`: secrets from sandbox stdout/stderr are redacted.
- `hidden_like_multiline`: multi-line shell=True, SQL variable interpolation before execute, and stored unobserved task.
- `ast_taint`: function/request taint flowing into shell and SQL sinks.
- `ignore_rule`: `# cr-agent: ignore=<rule_id>` suppression sample.
- `entropy_secret`: high-entropy literal secret redaction sample.
- `external_scanner`: external scanner finding merge sample for reports and database records.
- `resource_lifecycle_closed`: resource lifecycle sample with finally close for false-positive reduction.
- `secret_redaction_extended`: AWS, Slack, JWT, and generic token redaction sample.
- `sandbox_secret_output`: secrets in sandbox stdout/stderr are redacted.
