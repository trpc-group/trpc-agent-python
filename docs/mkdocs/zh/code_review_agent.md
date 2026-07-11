# 代码评审 Agent 设计

本文描述一个自动代码评审 Agent 的设计方案：它组合 Agent Skills、沙箱执行、结构化 findings、Filter 治理、监控审计和 SQL 存储。当前文档是设计和脚手架说明，用于在完整生产实现之前先明确架构、数据契约和安全边界。

## 概览

代码评审 Agent 接收 unified diff、PR patch 或本地仓库变更，并生成结构化评审报告。未来的可运行实现应支持以下输入：

- `--diff-file`：读取已保存的 unified diff 或 patch。
- `--repo-path`：读取本地 Git 仓库工作区变更。
- 测试 fixture：在 dry-run 或 fake-model 模式下运行确定性样本。

预期输出包括：

- `review_report.json`：机器可读的 findings、Filter 决策、沙箱运行记录和监控指标。
- `review_report.md`：面向人的评审摘要。
- SQL 记录：保存 review task、输入摘要、sandbox run、finding、Filter 决策、监控摘要和最终报告。

## 脚手架阶段的非目标

第一阶段贡献应保持小而可评审，不应宣称已经实现完整评审机器人。

- 不发布 GitHub / PR 评论。
- 不自动修改代码或应用修复。
- 不在宿主机直接执行模型生成的命令。
- 不包含生产调度器、Webhook 服务或凭证管理。
- 不实现完整沙箱、数据库和模型循环。

## 架构

完整实现建议采用如下流程：

```text
diff / repo changes
  -> diff parser
  -> code-review Skill
  -> Filter governance
  -> sandboxed checks
  -> structured finding validation
  -> dedupe and noise filtering
  -> SQLite storage
  -> JSON/Markdown report
```

这个设计把三类职责分开：

1. **评审策略** 放在 `code-review` Skill 和引用文档中。
2. **执行安全** 放在沙箱和 Filter 层中。
3. **可审计性** 通过结构化输出、SQL 存储和监控摘要实现。

## 可复用的现有能力

实现时应复用 tRPC-Agent 已有模式，而不是重新发明一套框架。

- Skills：[`trpc_agent_sdk/skills/__init__.py`](../../../trpc_agent_sdk/skills/__init__.py)
  - `SkillToolSet`
  - `create_default_skill_repository`
  - `SkillLoadTool`
  - `SkillRunTool`
- Skill 示例：
  - [`examples/skills`](../../../examples/skills)
  - [`examples/skills_with_container`](../../../examples/skills_with_container)
  - [`examples/skills_with_cube`](../../../examples/skills_with_cube)
- 代码执行：
  - [`BaseCodeExecutor`](../../../trpc_agent_sdk/code_executors/_base_code_executor.py)
  - [`ContainerCodeExecutor`](../../../trpc_agent_sdk/code_executors/container/_container_code_executor.py)
- Filter 治理：
  - [`FilterABC`](../../../trpc_agent_sdk/abc/_filter.py)
  - [`FilterResult`](../../../trpc_agent_sdk/abc/_filter.py)
- SQL 存储：
  - [`SqlStorage`](../../../trpc_agent_sdk/storage/_sql.py)
  - [`SqlKey`](../../../trpc_agent_sdk/storage/_sql.py)
  - [`SqlCondition`](../../../trpc_agent_sdk/storage/_sql.py)

## Skill 设计

`code-review` Skill 是可复用的评审策略包。第一阶段脚手架可以放在示例目录下：

```text
examples/code_review_agent/skills/code-review/
  SKILL.md
  references/
    finding_schema.md
    security_boundary.md
  scripts/
    # future static checks / diff helpers
```

`SKILL.md` 应定义评审工作流，并要求输出结构化结果。未来规则文档至少应覆盖 issue 中的 4 类问题：

- 安全风险。
- 异步错误。
- 资源泄漏。
- 测试缺失。
- 敏感信息泄漏。
- 数据库事务或连接生命周期问题。

Skill 不应要求模型修改文件。它应生成候选 findings，后续再由结构校验、去重、降噪和治理层决定哪些结果保留。

## 结构化 finding schema

每条高置信 finding 都应结构化。最小字段如下：

| 字段 | 说明 |
| --- | --- |
| `severity` | `info`、`low`、`medium`、`high` 或 `critical`。 |
| `category` | 例如 `security`、`async`、`resource_leak`、`test_coverage`、`secrets`、`database_lifecycle`。 |
| `file` | 仓库相对路径。 |
| `line` | diff 中新文件的行号。 |
| `title` | 一句话摘要。 |
| `evidence` | 具体 diff 或代码证据。 |
| `recommendation` | 可执行修复建议。 |
| `confidence` | `low`、`medium` 或 `high`。 |
| `source` | 例如 `skill`、`sandbox`、`filter` 或 `fake_model`。 |

未来可扩展字段：

- `fingerprint`：稳定去重键。
- `line_start` / `line_end`：行号范围。
- `needs_human_review`：是否需要人工复核后才能提升为正式 finding。
- `raw_source`：调试用来源信息。

低置信或证据不足的问题应进入 warnings 或 `needs_human_review`，不应混入高置信 findings。

## Diff 解析契约

Diff parser 应支持来自文件、PR patch 或本地 `git diff` 的 unified diff。它应生成适合模型阅读的紧凑表示，同时保留行号锚定所需结构。

推荐内部结构：

```text
DiffFile
  old_path
  new_path
  status
  hunks[]

DiffHunk
  old_start
  old_count
  new_start
  new_count
  changed_lines[]

ChangedLine
  old_line_number
  new_line_number
  kind: added | removed | context
  text
```

规则：

- finding 应尽量锚定到新文件 changed line。
- 纯删除行不应作为最终评论锚点。
- 重命名文件应保留 old path 和 new path。
- 二进制 diff 应记录为不支持或摘要，不应把原始二进制内容传给模型。

## 沙箱策略

生产路径应优先使用 Container 或 Cube/E2B。Local execution 只适合可信开发环境，不应作为处理不可信 diff 或模型生成命令的默认路径。

沙箱要求：

- 只读挂载仓库输入。
- 输出只能写到受控 workspace / output 目录。
- 设置命令超时。
- 限制输出大小。
- 只传入白名单环境变量。
- 不把 secrets 传入沙箱。
- 对 stdout、stderr、报告和 SQL 记录做敏感信息脱敏。
- 沙箱失败不能导致整个评审任务崩溃，应记录失败并继续生成报告。

`examples/skills_with_container` 是容器化 Skill 执行路径最接近的参考实现。

## Filter 治理

Filter 应在模型和沙箱执行前后承担策略网关角色。

执行前应 deny 或标记 `needs_human_review` 的情况：

- 高风险脚本或命令。
- 禁止访问的仓库路径。
- 非白名单网络访问。
- 超预算执行。
- dry-run 模式下需要不可用 secrets 的请求。

执行后应：

- 校验 finding schema。
- 丢弃无法锚定到 changed line 的 finding。
- 合并重复 finding。
- 降级低置信或推测性 finding。
- 脱敏 secrets。

每个 Filter 决策都应写入报告和数据库，包括决策、原因和 Filter 名称。

## SQLite schema 建议

最小 SQLite 实现可复用现有 SQL 存储模式。建议表：

### `review_tasks`

- `id`
- `repo_path`
- `base_ref`
- `head_ref`
- `mode`：`dry_run` 或 `apply`
- `status`
- `created_at`
- `completed_at`
- `metadata_json`

### `review_input_summaries`

- `id`
- `task_id`
- `diff_sha256`
- `file_count`
- `hunk_count`
- `changed_line_count`
- `summary_json`

### `sandbox_runs`

- `id`
- `task_id`
- `runtime`：`container`、`cube` 或 `local_dev`
- `command`
- `exit_code`
- `duration_ms`
- `stdout_excerpt`
- `stderr_excerpt`
- `status`

### `review_findings`

- `id`
- `task_id`
- `fingerprint`
- `severity`
- `category`
- `file`
- `line_start`
- `line_end`
- `title`
- `evidence`
- `recommendation`
- `confidence`
- `source`

### `filter_decisions`

- `id`
- `task_id`
- `finding_fingerprint`
- `filter_name`
- `decision`：`allow`、`deny`、`drop`、`merge`、`downgrade` 或 `needs_human_review`
- `reason`
- `created_at`

### `review_reports`

- `id`
- `task_id`
- `json_path`
- `markdown_path`
- `summary`
- `created_at`

### `monitoring_summaries`

- `id`
- `task_id`
- `total_duration_ms`
- `sandbox_duration_ms`
- `tool_call_count`
- `filter_interception_count`
- `finding_count`
- `severity_distribution_json`
- `exception_distribution_json`

## 去重和降噪

稳定的 finding fingerprint 应包含：

- 规范化文件路径；
- 行号范围或最近的 changed-line 锚点；
- category；
- 规范化 title / evidence；
- 可选 diff hunk hash。

评审流程不应对同一文件、同一行、同一类别重复输出 finding。低置信问题应进入 warnings 或人工复核区，而不是提升为高置信 finding。

## 监控和审计

报告应包含：

- 总评审耗时；
- 沙箱执行耗时；
- 工具调用次数；
- Filter 拦截次数；
- finding 数量；
- severity 分布；
- 异常类型分布；
- 沙箱失败和超时；
- Filter deny / human-review 决策。

审计事件应记录关键生命周期节点，例如 diff 收集、Skill 加载、沙箱命令请求、命令允许/拒绝、结构化解析成功/失败、数据库写入成功/失败、报告生成等。

## Dry-run 和 fake-model 模式

Dry-run 应作为默认模式。它应该：

- 解析 diff；
- 加载 Skill 策略；
- 在配置时执行沙箱策略决策；
- 校验和过滤 findings；
- 只写本地 artifact / 数据库记录；
- 生成报告；
- 不发布外部评论；
- 不修改仓库文件。

Fake-model 模式应允许在没有真实模型 API Key 的情况下测试完整链路。它可以从 fixture 返回确定性 findings，用于验证解析、存储、Filter 和报告生成。

## 后续实现阶段

1. **文档和脚手架**：设计文档、示例 README 和 `code-review` Skill 脚手架。
2. **解析器和 schema**：unified diff parser、Pydantic finding models、fake-model 路径。
3. **沙箱和存储**：container-first 沙箱 runner 和 SQLite 持久化。
4. **Filter 和指标**：去重、脱敏、人工复核分流、监控摘要和 8 个 fixture 测试。
5. **可选集成**：PR 评论、CI 入口、监控面板或 Cube/E2B 远程沙箱配置。
