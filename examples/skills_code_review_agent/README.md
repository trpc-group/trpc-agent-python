# 自动代码评审 Agent

本示例实现了一个可运行、可审计的自动代码评审 Agent。它读取 unified diff、Git 工作区、本地文件列表或测试 fixture，通过 Code Review Skill 执行确定性规则，并按需运行静态检查和单元测试。所有命令必须先经过 Filter，获准后才能进入隔离 workspace。最终结果会经过行号校验、敏感信息脱敏和去重，写入 JSON、Markdown 与数据库。

## 整体流程

```text
Diff / Git 工作区 / 文件列表 / Fixture
  → 输入规范化与摘要计算
  → 评审任务规划
  → Filter 前置决策
  → Container / Cube 沙箱执行
  → Finding 校验、脱敏与去重
  → JSON / Markdown 报告
  → SQLite / SQL 审计存储
```

单项检查超时、退出异常或沙箱启动失败时，评审任务会进入 `partial`，保留已经完成的检查和发现，不会因一个步骤失败而丢失整条审查记录。

## Skill 设计

Code Review Skill 位于 `skills/code-review/`，与 Agent 编排、Filter 和数据库层保持解耦：

- `SKILL.md`：声明能力、输入输出和安全边界。
- `rules/`：保存带稳定 `rule_id` 的 YAML 规则。
- `references/rules.md`：解释规则依据、误报边界和修复建议。
- `detectors/`：提供正则和 Python AST 检测器。
- `parser/`：解析 diff，并将检查范围限制在候选新增行。
- `scripts/`：提供可由沙箱直接调用的入口。
- `validators/`：保存候选问题的后续验证脚本。
- `runner.py`：加载规则、分派 detector 并输出结构化 Finding。

当前规则覆盖危险 shell 与动态执行、硬编码凭据、未跟踪异步任务、文件或数据库连接生命周期、测试缺失等类别。每个 Finding 至少包含：

```text
severity, category, file, line, title, evidence,
recommendation, confidence, source
```

规则还会携带版本和验证状态，便于定位产生结论的准确规则版本。

## 沙箱隔离策略

生产默认使用 SDK Container workspace runtime，并关闭网络；也可以接入 Cube runtime。`local` runtime 只用于开发回退，不是默认生产方案。

```text
agent/review.py
  → agent/task_planner.py     规划规则、静态检查和测试
  → agent/sandbox.py          装载源码和 Skill
  → sandbox/runner.py         执行 Filter 并调度任务
  → sandbox/executor/         调用统一 Workspace Runtime
  → Container / Cube
```

项目快照会排除 `.git`、`.venv`、`node_modules`、缓存、字节码和符号链接，并限制单文件及快照总大小。执行任务配置超时、内存、CPU、PID 和输出长度限制，执行后始终尝试清理 workspace。容器网络默认关闭，运行时不会联网安装依赖，也不会装载 Docker socket、宿主机凭据或 workspace 外路径。

## Filter 策略

Filter 是所有沙箱任务的前置关卡，并采用 fail-closed 策略：

```text
ExecutionRequest
  → 命令检查
  → 路径检查
  → 网络检查
  → 环境变量检查
  → 资源预算检查
  → allow / deny / needs_human_review
```

Filter 会拒绝非白名单命令、危险 shell 组合、受保护路径和非白名单网络目标；内联代码或超出资源阈值的任务进入人工复核。只有 `allow` 可以进入执行器，`deny` 和 `needs_human_review` 都会停在沙箱之外。每次决策都会记录风险等级、命中规则、原因、命令摘要和时间，并同时进入报告、数据库及 `filter_events.json`。

Filter 是应用层防线，不能代替 Container/Cube 提供的操作系统级隔离。

## 去重与降噪

所有候选 Finding 都会经过统一归一化流程：

1. 校验严重级别、类别、文件和候选新增行号。
2. 对标题、证据和修复建议进行敏感信息脱敏。
3. 按“文件、行号、类别”生成稳定指纹并去重。
4. 重复结果优先保留置信度更高、证据更完整的一项。
5. 低置信度或位置无效的问题进入 `needs_human_review`，不混入已确认 Finding。

该流程同时适用于规则、模型或其他分析器输出，防止不同来源重复报告同一问题。

## 安全边界

系统不会将 diff 内容拼接进 shell 命令，也不会向任务透传宿主机环境。环境变量采用白名单，网络采用默认拒绝；命令、stdout、stderr、Finding、warning 和最终报告在持久化前统一脱敏。

当前脱敏覆盖常见 API Key、OpenAI/GitHub Token、Bearer Token、JWT、AWS Access Key，以及 PostgreSQL、MySQL、MariaDB、MongoDB URL 中的密码。输出会截断到任务配置的最大长度，超时或执行异常会记录错误类型，但不会让整个评审流程崩溃。

## 监控字段

每次评审都会收集：

- 总耗时与各阶段耗时；
- 工具调用次数；
- Filter 拦截次数；
- Finding 总数；
- 各严重级别分布；
- 异常类型分布；
- 每个沙箱任务的状态、退出码、耗时和截断标识。

这些指标会写入报告和 `telemetry` 表，用于评测、监控、风险趋势分析和故障回放。

## 数据库 Schema

默认存储是 SQLite，运行时通过 SQLAlchemy 操作，接口可切换到 PostgreSQL 等 SQL 后端。SQLite 会显式启用外键，并为任务状态、创建时间、Finding 的 task id 和严重级别建立索引。

```text
review_task
├── skill_execution
├── sandbox_run
│   └── filter_event
├── finding
├── review_report
└── telemetry
```

- `review_task`：输入类型、摘要、digest、状态和起止时间。
- `skill_execution`：Skill、规则版本、detector 和执行摘要。
- `sandbox_run`：runtime、命令摘要、状态、日志、退出码和耗时。
- `filter_event`：Filter 决策、风险、原因和命中规则。
- `finding`：问题位置、证据、建议、置信度、规则版本和验证状态。
- `review_report`：最终 JSON、Markdown 与结论摘要。
- `telemetry`：耗时、调用、拦截、Finding 和异常分布。

可通过 `ReviewRepository.get_task(task_id)` 查询一次评审的完整执行轨迹。

## 输入与输出

CLI 支持以下输入方式：

- `--diff-file`：unified diff 或 PR patch 文件；
- `--repo-path`：Git 仓库相对于 `HEAD` 的未提交变更；
- `--fixture`：测试 fixture 文件或目录；
- `--file-list`：逐行保存项目相对文件路径的清单。

报告按 task id 分目录保存，避免覆盖历史结果：

```text
result/<task_id>/
├── review_report.json
├── review_report.md
└── filter_events.json
```

默认数据库为 `examples/skills_code_review_agent/result/review.db`。可以通过 `--db-url` 和 `--output-dir` 覆盖默认位置。

仓库输入示例：

```bash
python3 examples/skills_code_review_agent/run_agent.py \
  --repo-path . \
  --runtime container \
  --deterministic-only
```

diff fixture 与本地开发回退示例：

```bash
python3 examples/skills_code_review_agent/run_agent.py \
  --fixture examples/skills_code_review_agent/fixtures/security.diff \
  --runtime local \
  --fake-model
```

文件列表输入示例：

```bash
python3 examples/skills_code_review_agent/run_agent.py \
  --file-list ./review-files.txt \
  --runtime container \
  --deterministic-only
```

`--dry-run` 会完成输入解析、任务规划和 Filter 决策，但不会执行任何沙箱命令。报告会明确标注未执行检查，不会把“没有 Finding”描述成“代码没有问题”。

## 测试

```bash
PYTHONPATH=. pytest -q tests/code_review
```

测试覆盖无问题 diff、安全问题、异步资源泄漏、数据库连接生命周期、测试缺失、Finding 去重、沙箱失败、敏感信息脱敏、dry-run、任务规划、文件列表输入和 Filter 拦截等场景。
