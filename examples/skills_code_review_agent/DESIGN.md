# Skills Code Review Agent Design

## 背景与定位

本示例是 `examples` 级 deterministic 代码评审 Agent 原型，目标是展示如何把
Skill、输入解析、治理策略、沙箱适配、结构化 findings、SQLite 持久化和报告输出
组合成一个可验证闭环。它不修改 `trpc_agent_sdk/`，不新增 SDK 公共 API，也不把
代码评审能力抽象成框架级模块。所有实现都位于 `examples/skills_code_review_agent/`
内，便于独立运行、测试和后续演进。

默认执行路径是 dry-run，不依赖真实模型 API Key、Docker、Cube 或外部服务。
`container` 和 `cube` runtime 作为可选真实执行后端：环境可用时通过 SDK workspace runtime
执行规则脚本，环境不可用或未配置时记录为需要人工复核。
`local-dev` 没有隔离能力，因此必须显式传入 `--allow-local` 才能执行。

## 总体数据流

一次 review 从 CLI 开始：

```text
run_review.py
  -> input_parser
  -> skill_loader
  -> sandbox.run_rule_script
  -> review_rules
  -> governance decision
  -> report routing and dedupe
  -> SQLite store
  -> JSON / Markdown report
```

CLI 只负责参数解析、基本校验和打印结果；核心编排在 `agent/pipeline.py` 中完成。
pipeline 会把 `--diff-file`、`--repo-path` 或 `--fixture` 统一解析为
`InputSummary`，加载本地 `code-review` Skill manifest，执行规则脚本，收集
`FilterEvent` 与 `SandboxRun`，再生成 `ReviewReport` 并保存到 SQLite。

## Skill 设计

### 目录结构

`skills/code-review/` 是一个完整的本地 Skill 包：

```text
skills/code-review/
  SKILL.md
  README.md
  rules/
    security.md
    async-resource.md
    testing-db.md
    runtime.md
  scripts/
    rule_runner.py
```

`SKILL.md` 描述输入、输出、规则文档、脚本入口和安全边界。`skill_loader.py`
负责读取 front matter、校验规则和脚本路径、计算 digest，并输出 example-local
`SkillManifest`。这里没有调用 SDK 的 `SkillToolSet`，原因是当前示例要在无模型、
无真实 Agent session 的环境中稳定测试；但目录组织和 Skill 概念仍与 SDK Skill
体系保持一致。

### 规则脚本

`scripts/rule_runner.py` 是沙箱或 dry-run 中执行的命令入口。它接收规范化后的
`InputSummary` JSON 和 Skill manifest JSON，验证 schema 必要字段，然后调用
`agent.review_rules.run_review_rules()` 生成 deterministic findings。脚本支持直接执行
时的 import fallback，确保从示例目录运行 CLI 不需要安装额外包。

## 输入解析设计

`agent/input_parser.py` 把三类输入统一转换成 `InputSummary`：

- `--diff-file`：读取 UTF-8 unified diff。
- `--repo-path`：执行 `git diff`，并把 untracked 文本文件转换为 added-file diff。
- `--fixture`：读取 `fixtures/<name>.diff` 或显式 fixture 路径。

解析结果保留 file、old_path、status、hunk、context line、added/deleted line、
candidate line 和 binary marker。解析器对 malformed hunk 和 binary patch 采取
容错策略：记录 diagnostics，不让整个 review 崩溃。后续规则只扫描 added lines，
因此 candidate line 是 finding 定位的主要来源。

## Finding 与规则设计

核心输出模型是 `Finding`，字段覆盖：

- `severity`
- `category`
- `file`
- `line`
- `title`
- `evidence`
- `recommendation`
- `confidence`
- `source`
- `fingerprint`

规则引擎当前覆盖 hard-coded secret、`eval` / `exec` / `shell=True`、async client
生命周期、文件句柄生命周期、数据库连接生命周期和测试缺失。规则优先分析新增行，
避免对未改动上下文产生过多噪声。secret 规则支持已脱敏输入中的
`[REDACTED]` assignment 形态，这保证“落盘前脱敏”和“规则仍可检出 secret 类型”
两个安全目标不会互相冲突。

fingerprint 使用 category、file、line、title 和 redacted evidence 生成，供报告层和
SQLite 去重使用。低置信度 finding 不进入主 findings，而是进入 warnings。

## Governance 与 Sandbox 设计

`agent/governance.py` 实现 example-local 执行治理模型。它不是 SDK Filter 扩展，
而是把 Filter 概念落成可测试的 `FilterEvent`：

- `dry-run` 普通规则脚本允许执行。
- `local-dev` 未显式 `--allow-local` 时拒绝。
- `container` / `cube` 可用时进入 SDK workspace runtime；不可用或未配置时记录为
  `needs_human_review`。
- 高风险命令、网络命令、禁止路径、timeout 超预算、output limit 超预算会被拒绝或
  进入人工复核。

`agent/sandbox.py` 是统一执行入口，负责治理后的 runtime 分发、dry-run、本地 fallback 和
结果落盘。`agent/sandbox_workspace.py` 承接可选 SDK workspace runtime 适配，负责
container/cube 的 lazy 探测、最小 bundle 上传、远端 `rule_runner.py` 执行和 output spec
收集。任何 sandbox 失败都会转换成 `SandboxRun` 和诊断结果，不抛到 CLI 顶层导致整次
review 崩溃。

Container 默认使用 `python:3-slim` 并关闭网络；Cube 只从环境变量读取
`CUBE_TEMPLATE_ID`、`E2B_API_URL` 和 `E2B_API_KEY`，避免把凭证放进 CLI 参数、报告或
数据库。两类远端后端都只 stage `models.py`、`review_rules.py`、`sanitizer.py`、
`rule_runner.py`、脱敏后的输入和 manifest，不上传完整仓库。

## 安全边界

安全边界分为四层：

1. 输入解析只保存 summary 和 diff hash，不把完整原始 diff 写入 SQLite。
2. 治理层在执行前检查 runtime、路径、命令、网络、环境变量白名单和预算。
3. sandbox 层执行 timeout 控制、stdout/stderr 捕获和 output size 截断。
4. sanitizer 在 findings、filter events、sandbox output、report 和 store 写入前脱敏。

脱敏覆盖 Bearer token、API key、secret/token/password assignment、private key block
和常见连接串密码。需要注意，静态治理和脱敏不是完整 sandbox；真正的生产隔离仍应由
Container、Cube 或等价受控 runtime 提供。

## SQLite 持久化设计

`agent/store.py` 使用标准库 `sqlite3`，避免给 example 引入 ORM 复杂度。它同时暴露最小
store protocol 和 factory 注入点；SQLite 是默认实现，pipeline 可注入其他 SQL 后端而不改变
CLI 或报告契约。最小 schema 包括：

- `review_task`
- `input_diff`
- `finding`
- `filter_event`
- `sandbox_run`
- `report`
- `review_metrics`

数据库保存 `InputSummary` JSON 和 diff SHA-256，而不是完整 raw diff。`review_metrics`
独立保存已有 `ReviewMetrics` 的计数和分布字段，便于不解析 report JSON 也能查询监控摘要。finding 表按
`task_id + fingerprint + route` 去重，route 区分主 findings、warnings 和
needs human review。查询 helper 支持按 task id 读取 task、input summary、finding、
filter event、sandbox run 和 report。

## 报告与降噪设计

`agent/report.py` 负责去重、路由和渲染：

- `dedupe_findings()` 基于 fingerprint 去重。
- `route_findings()` 将低置信度问题放入 warnings。
- governance 或 sandbox 不确定性会合成 `GOVERNANCE` 或 `SANDBOX` 类型的人工复核项。
- `build_review_report()` 汇总 metrics、severity/category 分布、异常分布和结论。
- `write_review_report()` 输出 JSON 和 Markdown。

报告结论根据高危 finding、人工复核项、warning 数量决定。Markdown 保持简单可读，
不引入模板引擎。

## 监控与审计字段

`ReviewMetrics` 记录：

- 总耗时
- sandbox 耗时
- tool call 数
- interception 数
- finding / warning / human-review 数
- severity 分布
- category 分布
- exception 分布

这些字段用于展示每次 review 的风险和执行状态，也为后续 Evaluation、回放或优化闭环
保留基础数据。

## 测试策略

测试只关注业务代码行为，不验证 README 或 sample output。8 条公开 fixture 位于
`fixtures/`，由端到端业务测试驱动，覆盖：

- clean diff
- security issue
- async leak
- DB lifecycle
- missing tests
- duplicate finding
- sandbox failure
- secret redaction

其余测试覆盖 parser、Skill loader、rule runner contract、governance、sandbox、
pipeline、store、report 和 sanitizer。质量门禁测试用公开 deterministic corpus 固定高危召回、
高置信误报和脱敏率代理指标。所有测试默认不依赖真实模型、Docker 或 Cube；真实 runtime 成功路径
通过 fake workspace runtime 覆盖。

## 已知边界

- 当前没有真实 LLM planner/executor 调用。
- 当前没有 SDK Filter 注册或 SDK CodeExecutor 扩展。
- `container` / `cube` 真实后端依赖本机 Docker 或 Cube/E2B 配置；默认测试环境使用 fake 或
  unavailable 分支，不要求外部服务。
- 规则是启发式 deterministic 检查，目标是可测试原型，不是完整静态分析器。
- SQLite schema 是 example-local v1，当前不提供 migration 机制。
