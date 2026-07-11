# Code Review Agent Example

This example is a **design and scaffold** for an automatic code-review Agent. It is intentionally not a complete runnable bot yet.

The goal is to show how a future implementation can combine tRPC-Agent Skills, sandbox execution, Filter governance, structured findings, monitoring, and SQL storage.

## What this example demonstrates

- A Skill-first review policy package under `skills/code-review/`.
- A container-first sandbox design for executing static checks or helper scripts.
- A structured finding contract that can be validated, deduplicated, stored, and rendered.
- A dry-run-first workflow that works without posting comments or modifying code.
- A staged implementation path for future MVP work.

## Current status

This directory currently contains documentation and a Skill scaffold only.

It does **not** yet include:

- a runnable `run_review.py` CLI;
- a diff parser;
- SQLite models;
- real model calls;
- PR/GitHub comment posting;
- automatic code fixes.

Those pieces are intentionally left for follow-up MVP work after the design is reviewed.

## Architecture

```text
user / CI dry-run request
  -> collect git diff or patch file
  -> parse files, hunks, and changed lines
  -> load skills/code-review/SKILL.md
  -> apply Filter governance before sandbox runs
  -> run approved checks in container or Cube/E2B sandbox
  -> validate structured findings
  -> dedupe and downgrade noisy findings
  -> persist task, sandbox runs, findings, and metrics
  -> render review_report.json and review_report.md
```

## Planned folder layout

```text
examples/code_review_agent/
  README.md
  skills/
    code-review/
      SKILL.md
      references/
        finding_schema.md
        security_boundary.md
      scripts/
        # future static checks / diff helpers
  agent/
    # future MVP modules:
    # diff_parser.py
    # schema.py
    # filters.py
    # storage.py
    # sandbox.py
  fixtures/
    # future diff samples
```

## Skill package

The `code-review` Skill defines how the Agent should inspect code changes. It should be loaded with `skill_load` when a review task starts and can later provide approved scripts through `skill_run`.

The Skill focuses on these review categories:

- security risks;
- async errors;
- resource leaks;
- missing tests;
- sensitive information leaks;
- database transaction or connection lifecycle issues.

The Skill must return structured findings and must not instruct the Agent to modify files.

## Dry-run lifecycle

Dry-run is the default target mode for this example.

A future dry-run command should:

1. Read a diff from `--diff-file` or local repository changes.
2. Parse changed files and line anchors.
3. Load the `code-review` Skill.
4. Run only approved sandbox checks.
5. Validate all findings against the documented schema.
6. Deduplicate repeated findings.
7. Route low-confidence issues to warnings or `needs_human_review`.
8. Write local reports and optional SQLite records.
9. Avoid external comments, pushes, or file modifications.

## Security checklist

A production implementation should enforce these defaults:

- Use container or Cube/E2B runtime for untrusted command execution.
- Treat local runtime as a development fallback only.
- Mount repository inputs read-only.
- Use a controlled output directory.
- Enforce timeouts and output-size limits.
- Pass only allowlisted environment variables.
- Never pass secrets into the sandbox.
- Redact API keys, tokens, passwords, and credentials in reports and database rows.
- Record sandbox failures and Filter denials without crashing the full review.

## Future MVP tasks

A minimal runnable follow-up can add:

- `agent/diff_parser.py`: parse unified diffs into files, hunks, and changed lines.
- `agent/schema.py`: Pydantic models for review findings, reports, and metrics.
- `agent/filters.py`: changed-line anchoring, dedupe, noise filtering, and redaction.
- `agent/storage.py`: SQLite-backed review task and finding storage.
- `agent/sandbox.py`: container-first sandbox wrapper with timeout and output limits.
- `run_review.py`: dry-run CLI supporting `--diff-file`, `--repo-path`, and `--fake-model`.
- `fixtures/`: at least 8 diff samples covering clean diff, security issue, async/resource leak, database lifecycle, missing tests, duplicate finding, sandbox failure, and secret redaction.

## Learning path for new contributors

If you are new to this project, read these in order:

1. [Skills example](../skills/README.md) - basic Skill loading and `skill_run`.
2. [Skills with container](../skills_with_container/README.md) - sandboxed Skill execution with Docker.
3. [Agent Skills docs](../../docs/mkdocs/en/skill.md) - Skill architecture and loading model.
4. [Code Executor docs](../../docs/mkdocs/en/code_executor.md) - local, container, and Cube/E2B execution.
5. [Filter docs](../../docs/mkdocs/en/filter.md) - request/response governance.
6. [SQL Session docs](../../docs/mkdocs/en/session_sql.md) - SQL persistence patterns.
7. [Code Review Agent design](../../docs/mkdocs/en/code_review_agent.md) - the architecture this example follows.

---

# 中文说明

# 代码评审 Agent 示例

本示例是一个自动代码评审 Agent 的**设计说明和脚手架**，目前还不是完整可运行的机器人。

它的目标是说明：未来如何把 tRPC-Agent 的 Skills、沙箱执行、Filter 治理、结构化 findings、监控审计和 SQL 存储组合成一个完整的代码评审 Agent。

## 这个示例展示什么

- 在 `skills/code-review/` 下放置一个以 Skill 为核心的代码评审策略包。
- 使用 container-first 的沙箱设计来执行静态检查或辅助脚本。
- 定义结构化 finding 契约，方便后续校验、去重、入库和渲染报告。
- 以 dry-run 为默认工作流，不发布评论、不修改代码。
- 为后续 MVP 工作提供分阶段实现路径。

## 当前状态

当前目录只包含文档和 Skill 脚手架。

它**暂时不包含**：

- 可运行的 `run_review.py` 命令行入口；
- diff 解析器；
- SQLite 数据模型；
- 真实模型调用；
- PR / GitHub 评论发布；
- 自动代码修复。

这些内容会留到设计方案被 review 后，在后续 MVP PR 中逐步实现。

## 架构

```text
用户 / CI 发起 dry-run 请求
  -> 收集 git diff 或 patch 文件
  -> 解析文件、hunk 和变更行
  -> 加载 skills/code-review/SKILL.md
  -> 在沙箱执行前应用 Filter 治理
  -> 在 Container 或 Cube/E2B 沙箱中执行已批准的检查
  -> 校验结构化 findings
  -> 对重复和噪声 findings 去重 / 降级
  -> 持久化 task、sandbox run、findings 和 metrics
  -> 生成 review_report.json 和 review_report.md
```

## 计划目录结构

```text
examples/code_review_agent/
  README.md
  skills/
    code-review/
      SKILL.md
      references/
        finding_schema.md
        security_boundary.md
      scripts/
        # 未来放静态检查 / diff 辅助脚本
  agent/
    # 未来 MVP 模块：
    # diff_parser.py
    # schema.py
    # filters.py
    # storage.py
    # sandbox.py
  fixtures/
    # 未来放 diff 测试样例
```

## Skill 包

`code-review` Skill 定义了 Agent 应该如何检查代码变更。评审任务开始时可以通过 `skill_load` 加载它，后续可以通过 `skill_run` 执行经过批准的脚本。

这个 Skill 重点关注这些评审类别：

- 安全风险；
- 异步错误；
- 资源泄漏；
- 测试缺失；
- 敏感信息泄漏；
- 数据库事务或连接生命周期问题。

这个 Skill 必须返回结构化 findings，并且不能要求 Agent 修改文件。

## Dry-run 生命周期

Dry-run 是本示例的默认目标模式。

未来的 dry-run 命令应该：

1. 从 `--diff-file` 或本地仓库变更读取 diff。
2. 解析变更文件和行号锚点。
3. 加载 `code-review` Skill。
4. 只运行经过批准的沙箱检查。
5. 根据文档中的 schema 校验所有 findings。
6. 对重复 findings 去重。
7. 将低置信问题放入 warnings 或 `needs_human_review`。
8. 写入本地报告和可选 SQLite 记录。
9. 不发布外部评论、不 push、不修改文件。

## 安全检查清单

生产实现应强制执行以下默认策略：

- 使用 Container 或 Cube/E2B runtime 执行不可信命令。
- 只把本地 runtime 当作开发 fallback。
- 以只读方式挂载仓库输入。
- 使用受控输出目录。
- 强制设置超时和输出大小限制。
- 只传入白名单环境变量。
- 不要把 secrets 传入沙箱。
- 在报告和数据库记录中脱敏 API key、token、password 和凭证。
- 记录沙箱失败和 Filter 拒绝原因，但不要让整个评审任务崩溃。

## 后续 MVP 任务

后续最小可运行版本可以增加：

- `agent/diff_parser.py`：把 unified diff 解析成文件、hunk 和变更行。
- `agent/schema.py`：定义 review findings、reports 和 metrics 的 Pydantic 模型。
- `agent/filters.py`：实现变更行锚定、去重、降噪和脱敏。
- `agent/storage.py`：基于 SQLite 的 review task 和 finding 存储。
- `agent/sandbox.py`：container-first 沙箱封装，支持超时和输出限制。
- `run_review.py`：dry-run 命令行入口，支持 `--diff-file`、`--repo-path` 和 `--fake-model`。
- `fixtures/`：至少 8 个 diff 样例，覆盖无问题 diff、安全问题、异步 / 资源泄漏、数据库生命周期、测试缺失、重复 finding、沙箱失败和敏感信息脱敏。

## 新贡献者学习路径

如果你刚接触这个项目，建议按顺序阅读：

1. [Skills 示例](../skills/README.md) - 基础 Skill 加载和 `skill_run`。
2. [容器版 Skills 示例](../skills_with_container/README.md) - 使用 Docker 沙箱执行 Skill。
3. [Agent Skills 文档](../../docs/mkdocs/en/skill.md) - Skill 架构和加载模型。
4. [Code Executor 文档](../../docs/mkdocs/en/code_executor.md) - local、container 和 Cube/E2B 执行。
5. [Filter 文档](../../docs/mkdocs/en/filter.md) - 请求 / 响应治理。
6. [SQL Session 文档](../../docs/mkdocs/en/session_sql.md) - SQL 持久化模式。
7. [Code Review Agent 设计](../../docs/mkdocs/en/code_review_agent.md) - 本示例遵循的架构设计。
