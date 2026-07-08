# 代码评审 Skill

这个 Skill 用于审查 git diff 和 PR patch。

## 工作流

1. 把 unified diff 解析为变更文件、hunk、上下文和候选新增行。
2. 应用 `docs/rules.md` 和 `rules.json` 中的确定性评审规则、AST 辅助规则和轻量 taint 规则。
3. 只有通过 Filter 策略后，才运行 `scripts/` 下的沙箱安全脚本。
4. 返回结构化 finding，包含 severity、category、file、line、evidence、recommendation、confidence 和 source。
5. 持久化 task、sandbox run、Filter 决策、finding、监控摘要和最终报告。

该 Skill 优先支持离线 dry-run。生产使用时应在 Container 或 Cube/E2B workspace runtime 中运行脚本；本地执行只作为显式开发 fallback。

## 脚本

- `scripts/diff_summary.py`：读取 unified diff，输出文件、hunk 和新增行统计。
- `scripts/static_review.py`：执行轻量静态检查，统计危险模式命中。
- `scripts/test_probe.py`：判断源码变更是否有对应测试变更。
- `scripts/scanner_probe.py`：默认运行离线 bandit、ruff、detect-secrets，并把输出规范化为 scanner findings；semgrep auto 需要显式开启并先经过网络 Filter。
- `scripts/unit_test_probe.py`：在沙箱策略允许时执行配置的单元测试命令。

自定义规则脚本必须位于本 Skill 的 `scripts/` 目录下，并通过 Filter preflight 后才能通过 `--custom-rule-script` 执行。

`filter_policy.json` 定义 Filter policy-as-code，包括禁止路径、高风险命令、预算和 workspace 路径 allowlist。代码中可使用 `# cr-agent: ignore=<rule_id>` 忽略当前行或下一行的特定规则。

`agent/native_agent.py` 提供 `code_review_tool` 和 `create_code_review_agent(model=..., skill_workspace_runtime=...)`，可把完整流水线挂载为 tRPC-Agent `FunctionTool`，并在传入 workspace runtime 后暴露 SDK 原生 `skill_load` / `skill_run`。

`agent/native_filter.py` 提供 `CodeReviewGovernanceFilter` 和 `create_review_filter()`，可把同一套 `ReviewFilterPolicy` 作为 tRPC-Agent `BaseFilter` 复用。`ReviewStore.from_url()` 支持 `sqlite:///...`，并为 Postgres/MySQL 保留 SQL 后端扩展点。

## 使用示例

dry-run 审查：

```bash
python examples/skills_code_review_agent/run_review.py --fixture security_issue --dry-run
```

Container 沙箱审查：

```bash
python examples/skills_code_review_agent/run_review.py --fixture security_issue --sandbox container --container-image python:3-slim
```

自定义规则脚本：

```bash
python examples/skills_code_review_agent/run_review.py --fixture security_issue --dry-run --custom-rule-script scripts/static_review.py
```

## 安全约束

所有命令执行前必须经过 Filter。被 deny 或标记为 `needs_human_review` 的请求必须写入报告和数据库，并且不能进入沙箱执行。

# Code Review Skill

Use this skill to review git diffs and pull-request patches.

## Workflow

1. Parse the unified diff into changed files, hunks, context, and added candidate lines.
2. Apply deterministic, AST-assisted, and lightweight taint rules from `docs/rules.md` and `rules.json`.
3. Run sandbox-safe scripts from `scripts/` only after filter policy approval.
4. Return structured findings with severity, category, file, line, evidence, recommendation, confidence, and source.
5. Persist task, sandbox runs, filter decisions, findings, monitoring summary, and final report.

The skill is designed for offline dry-run mode first. Production use should run scripts in a Container or Cube/E2B workspace runtime. Local execution is a development fallback only.

## Scripts

- `scripts/diff_summary.py`: reads a unified diff and prints file/hunk/addition counts.
- `scripts/static_review.py`: performs lightweight static checks for dangerous patterns.
- `scripts/test_probe.py`: reports whether source changes have matching test changes.
- `scripts/scanner_probe.py`: runs offline bandit, ruff, and detect-secrets by default; semgrep auto is opt-in and must pass the network Filter first.
- `scripts/unit_test_probe.py`: runs a configured unit test command when the sandbox policy allows it.

Custom rule scripts can be selected with `--custom-rule-script` when they live under this Skill's `scripts/` directory and pass Filter preflight.

`filter_policy.json` defines Filter policy-as-code, including forbidden paths, high-risk commands, budgets, and workspace path allowlists. Changed code can use `# cr-agent: ignore=<rule_id>` to suppress a specific rule on the current or next line.

`agent/native_agent.py` provides `code_review_tool` and `create_code_review_agent(model=..., skill_workspace_runtime=...)` so the full pipeline can be attached as a tRPC-Agent `FunctionTool`; when a workspace runtime is supplied, the agent also exposes SDK-native `skill_load` / `skill_run`.

`agent/native_filter.py` provides `CodeReviewGovernanceFilter` and `create_review_filter()` so the same `ReviewFilterPolicy` can be reused as a tRPC-Agent `BaseFilter`. `ReviewStore.from_url()` supports `sqlite:///...` and keeps Postgres/MySQL SQL backend extension points explicit.

## Usage Examples

Dry-run review:

```bash
python examples/skills_code_review_agent/run_review.py --fixture security_issue --dry-run
```

Container sandbox review:

```bash
python examples/skills_code_review_agent/run_review.py --fixture security_issue --sandbox container --container-image python:3-slim
```

Custom rule script:

```bash
python examples/skills_code_review_agent/run_review.py --fixture security_issue --dry-run --custom-rule-script scripts/static_review.py
```

## Safety

All command execution must pass the review filter before sandbox execution. Denied or `needs_human_review` decisions must be recorded in the report and database and must not execute.
