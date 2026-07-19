# 自动代码评审 Agent 开发计划

## 1. 背景和价值

tRPC-Agent 已经提供了构建自动代码评审系统所需的关键基础设施：

- Skill 体系可以把可复用工作流封装为 `SKILL.md`、规则文档和脚本，并通过 `skill_load`、`skill_run` 在隔离 workspace 中执行。
- CodeExecutor 和 Workspace Runtime 支持本地、Container、Cube/E2B 等执行环境，能够为脚本运行、静态检查和测试提供沙箱隔离。
- Session / Memory / SQL 存储能力可以承载评审任务状态、审查历史、执行日志、结构化 findings 和最终报告。
- Filter 和 Telemetry 可以对工具调用、模型调用、沙箱执行进行拦截、审计、计数和异常观测。

因此，这个题目的重点不是“让模型像人一样评论代码”，而是将 Skill、沙箱、规则、治理、存储、报告和监控串成一条可运行、可验证、可回放、可审计的工程链路。该原型一旦完成，后续不仅可以作为公开示例，还能进一步演进为评测基座、CI 审查助手、风险扫描器和回放测试对象。

## 2. 目标和范围

### 2.1 总体目标

实现一个自动代码评审 Agent 原型，支持以下完整链路：

1. 读取 `git diff`、PR patch 或本地工作区变更。
2. 通过 `code-review` Skill 加载规则和脚本。
3. 在 Filter 策略允许后进入 Container 或 Cube/E2B 沙箱执行必要检查。
4. 产出结构化 findings，并区分高置信 findings、warnings 和 `needs_human_review`。
5. 将 review task、input diff 摘要、sandbox run、filter 决策、findings 和最终报告写入 SQLite。
6. 输出 `review_report.json` 和 `review_report.md`。
7. 支持 `dry-run / fake model`，在没有真实模型 API Key 时仍能验证解析、沙箱和落库链路。

### 2.2 本期范围

本期优先实现一个工程上可验证的 MVP，重点保证：

- 8 条公开样例可完整运行。
- 高危问题具备确定性检出能力。
- 沙箱、Filter、脱敏、落库和报告链路完整。
- 结果字段、监控字段和数据库记录对齐验收标准。

### 2.3 非目标

以下内容不作为首期核心目标：

- 依赖真实大模型才可运行的审查逻辑。
- 复杂的跨 PR 历史学习与长期 Memory 优化。
- 大规模多仓库并发调度平台。
- 高级 UI、Web 面板或外部监控平台接入。

## 3. 方案原则

### 3.1 确定性优先

高危问题识别、脱敏、去重、Filter 决策、沙箱失败处理等环节必须以确定性逻辑为主，不能将正确性完全依赖于模型发挥。

### 3.2 复用框架优先

尽量复用仓库已有的 Skill、Workspace Runtime、Filter、SqlStorage、Runner 等能力，避免在 SDK 核心层做大改动。

### 3.3 沙箱默认生产、Local 仅开发回退

生产默认执行环境必须是 Container 或 Cube/E2B。Local runtime 只在本地调试、单元测试或环境缺失时作为显式 fallback。

### 3.4 先打通链路，再增强智能

第一阶段先保证解析、规则、落库、报告、脱敏、超时和 Filter 审批链路可跑；后续如需模型增强，只作为补充说明或建议生成层。

## 4. 目标目录结构

```text
examples/skills_code_review_agent/
  README.md
  DEVELOPMENT_PLAN.md
  run_agent.py
  agent/
    __init__.py
    agent.py
    config.py
    prompts.py
    tools.py
  skills/
    code-review/
      SKILL.md
      RULES.md
      scripts/
        parse_diff.py
        run_linters.py
        run_tests.py
  src/
    __init__.py
    review_types.py
    input_loader.py
    diff_parser.py
    rule_engine.py
    deduper.py
    redactor.py
    filter_policy.py
    telemetry.py
    report_writer.py
    storage/
      __init__.py
      models.py
      repository.py
      init_db.py
  tests/
    __init__.py
    test_code_review_agent.py
    fixtures/
      clean.diff
      security_issue.diff
      async_resource_leak.diff
      db_lifecycle_issue.diff
      missing_tests.diff
      duplicate_finding.diff
      sandbox_failure.diff
      secret_redaction.diff
```

## 5. 模块拆解

## 5.1 Agent 编排层

### 职责

- 接收命令行参数和输入源。
- 初始化模型、SkillToolSet、runtime、存储和过滤策略。
- 驱动“输入解析 -> 规则评审 -> Filter 决策 -> 沙箱执行 -> 汇总结果 -> 落库 -> 报告输出”主流程。

### 复用能力

- `LlmAgent`
- `Runner`
- Skill 基础设施
- 仓库中的容器和 Cube runtime 创建方式

### 需要新写或调整

- `agent/agent.py`
- `agent/config.py`
- `agent/prompts.py`
- `agent/tools.py`
- `run_agent.py`

## 5.2 Skill 层

### 职责

- 以 `code-review` Skill 的形式封装可复用的代码评审说明、规则文档和脚本。
- 通过 `SKILL.md` 定义 Skill 名称、用途、何时调用、输入输出约定。
- 通过脚本目录承载 diff 解析、静态检查、测试执行等可复用工作流。

### 复用能力

- `skill_load`
- `skill_run`
- Skill repository
- 隔离 workspace 目录结构

### 需要新写或调整

- `skills/code-review/SKILL.md`
- `skills/code-review/RULES.md`
- `skills/code-review/scripts/*.py`

## 5.3 输入解析层

### 职责

- 支持 `--diff-file`、`--repo-path` 和测试 fixture。
- 统一解析为内部 `ReviewInput` 模型。
- 从 unified diff 中提取变更文件、hunk、上下文和候选行号。

### 需要新写

- `src/review_types.py`
- `src/input_loader.py`
- `src/diff_parser.py`

### 关键输出

- 变更文件列表
- 每个 hunk 的起止行和上下文
- 新增行、修改行和候选审查行
- 输入摘要信息，供数据库和报告使用

## 5.4 规则引擎层

### 职责

- 对解析后的输入执行确定性规则检查。
- 产出结构化 findings。
- 提供初步 severity、confidence、source 和 recommendation。

### 规则覆盖目标

首期建议覆盖以下 6 类，至少满足 issue 要求中的 4 类：

- 安全风险
- 异步错误
- 资源泄漏
- 测试缺失
- 敏感信息泄漏
- 数据库事务或连接生命周期问题

### 需要新写

- `src/rule_engine.py`
- `src/deduper.py`

### 输出约束

每条 finding 至少包含：

- `severity`
- `category`
- `file`
- `line`
- `title`
- `evidence`
- `recommendation`
- `confidence`
- `source`

## 5.5 去重和降噪层

### 职责

- 同一文件、同一行、同一类问题不重复报。
- 低置信度问题进入 `warnings` 或 `needs_human_review`。
- 避免重复脚本输出和重复规则命中污染最终报告。

### 需要新写

- `src/deduper.py`

### 推荐策略

- 去重主键：`category + file + line + normalized_evidence`
- 高置信 findings：`confidence >= 0.8`
- 人工复核项：`0.4 <= confidence < 0.8`
- 低置信 warnings：`confidence < 0.4`

## 5.6 沙箱执行层

### 职责

- 在受控执行环境中运行静态检查脚本、单元测试或自定义规则脚本。
- 统一采集命令、耗时、退出码、stdout/stderr 摘要和错误信息。

### 复用能力

- Container Workspace Runtime
- Cube/E2B Workspace Runtime
- `skill_run`

### 需要新写或调整

- runtime 选择配置
- 沙箱执行参数和兜底逻辑
- 输出采样和摘要写入逻辑

### 实施要求

- Container 或 Cube 作为默认生产方案
- Local 只能通过显式配置作为开发回退
- 每次执行必须支持 timeout 和输出大小限制
- 沙箱失败不能导致整个任务崩溃

## 5.7 安全边界和 Filter 治理层

### 职责

- 在沙箱执行前进行风险决策。
- 对高风险脚本、禁止路径、非白名单网络访问和超预算执行进行前置拦截。
- 将拦截原因写入数据库和报告。

### 复用能力

- Filter 机制
- callback 和事件钩子

### 需要新写

- `src/filter_policy.py`

### 最低治理规则

- 禁止访问敏感路径
- 禁止明显危险命令组合
- 限制非白名单网络访问
- 限制超大 diff、超多文件、超长运行时和超多沙箱调用
- `deny / needs_human_review` 状态不得直接进入沙箱执行

## 5.8 脱敏层

### 职责

- 对报告、日志、数据库写入和 evidence 字段进行敏感信息脱敏。
- 防止明文 API Key、token、password 等出现在输出结果中。

### 需要新写

- `src/redactor.py`

### 最低要求

- 对 diff 摘要、stdout/stderr、evidence、recommendation、Markdown 报告和数据库入库内容统一脱敏
- 检出率目标对齐验收标准：`>= 95%`

## 5.9 数据库存储层

### 职责

- 保存 review task、input diff 摘要、sandbox run、finding、最终报告和监控摘要。
- 支持按 `task_id` 查询整条评审链路。

### 复用能力

- `SqlStorage`
- SQLite 作为默认 SQL 实现

### 需要新写

- `src/storage/models.py`
- `src/storage/repository.py`
- `src/storage/init_db.py`

### 最小 schema 设计

建议至少包含以下实体：

- `review_tasks`
- `review_inputs`
- `filter_decisions`
- `sandbox_runs`
- `findings`
- `review_reports`

### 关键字段建议

#### `review_tasks`

- `task_id`
- `status`
- `input_type`
- `runtime_type`
- `dry_run`
- `created_at`
- `finished_at`
- `total_duration_ms`
- `error_type`

#### `review_inputs`

- `task_id`
- `diff_sha256`
- `changed_files_count`
- `hunk_count`
- `candidate_line_count`
- `input_summary`

#### `filter_decisions`

- `task_id`
- `decision`
- `reason_code`
- `reason_text`
- `target`
- `created_at`

#### `sandbox_runs`

- `task_id`
- `run_name`
- `command`
- `exit_code`
- `timed_out`
- `duration_ms`
- `stdout_summary`
- `stderr_summary`

#### `findings`

- `task_id`
- `fingerprint`
- `severity`
- `category`
- `file`
- `line`
- `title`
- `evidence`
- `recommendation`
- `confidence`
- `source`
- `needs_human_review`

#### `review_reports`

- `task_id`
- `report_json`
- `report_markdown`
- `monitoring_summary`
- `final_verdict`

## 5.10 报告和监控层

### 职责

- 输出 `review_report.json` 和 `review_report.md`
- 汇总监控指标和严重级别分布
- 提供可读、可验收、可归档的最终结论

### 需要新写

- `src/report_writer.py`
- `src/telemetry.py`

### 报告必须包含

- findings 摘要
- 严重级别统计
- 人工复核项
- Filter 拦截摘要
- 监控指标
- 沙箱执行摘要
- 可执行修复建议

## 5.11 Agent / Skill / Filter / Storage / Report 职责对照

这一节用于明确不同模块的职责边界，避免后续把编排逻辑、规则逻辑、治理逻辑和输出逻辑混写在一起。

### 5.11.1 对照表

| 模块 | 主要职责 | 应放入的内容 | 不应放入的内容 | 当前目录落点 |
| --- | --- | --- | --- | --- |
| Agent | 编排整条评审链路，驱动输入、规则、Filter、沙箱、落库和报告 | 主流程函数、运行配置、模块调用顺序、失败兜底、任务状态流转 | 具体规则细节、具体脱敏规则、数据库 schema 细节、Markdown 模板细节 | `run_agent.py`、`agent/agent.py`、`agent/config.py` |
| Skill | 封装可复用的代码评审能力包 | `SKILL.md`、规则文档、脚本目录、输入输出约定、能力说明 | 全局任务状态管理、数据库写入、总报告生成、全局 Filter 决策 | `skills/code-review/` |
| Rule Engine | 在结构化 diff 上运行确定性或启发式规则 | 风险模式匹配、finding 生成、初始 severity / confidence | 直接执行高风险命令、数据库写入、CLI 参数解析 | `src/rule_engine.py` |
| Filter | 对待执行动作做前置治理和审批决策 | 高风险脚本判断、禁止路径检查、预算限制、网络访问限制 | 直接做业务风险识别、最终报告排版、数据库 schema 设计 | `src/filter_policy.py` |
| Sandbox | 在受控环境中执行脚本和检查 | lint/test/脚本执行、timeout、输出限制、执行记录 | 决定是否允许执行、最终结论生成 | `agent/tools.py`、Skill scripts、后续 runtime 封装 |
| Storage | 持久化任务、执行记录和结果 | schema、repository、初始化脚本、按 `task_id` 查询 | 规则匹配、CLI 解析、Markdown 报告排版 | `src/storage/` |
| Report | 生成对外可读结果和监控摘要 | JSON/Markdown 报告、严重级别统计、人工复核项、监控字段汇总 | 规则命中逻辑、Filter 准入判断、数据库底层建表 | `src/report_writer.py`、`src/telemetry.py` |
| Redactor | 脱敏所有可能外显或入库的敏感内容 | evidence、stdout/stderr、报告、数据库写入前内容脱敏 | 风险分类、任务调度、报告结构设计 | `src/redactor.py` |

### 5.11.2 模块关系

推荐的数据流关系如下：

```text
CLI / fixture
  -> Agent
     -> Input Loader
     -> Diff Parser
     -> Rule Engine
     -> Deduper
     -> Filter
     -> Sandbox
     -> Redactor
     -> Storage
     -> Report
```

含义如下：

- Agent 是总控，不是所有业务逻辑的承载者。
- Skill 是能力包，通过文档和脚本提供可复用能力，但不负责全局任务编排。
- Rule Engine 只负责“发现问题”，不负责“决定能否执行脚本”。
- Filter 只负责“动作治理”，不负责“代码风险识别”。
- Storage 负责“沉淀状态和结果”，不负责“得出结论”。
- Report 负责“对外呈现”，不负责“底层规则判断”。

### 5.11.3 本项目中的放置原则

后续开发时应遵循以下放置原则：

- 如果某段逻辑决定“先做什么、后做什么、失败怎么办”，放在 Agent。
- 如果某段内容是“这类审查能力怎么复用、有哪些脚本和规则说明”，放在 Skill。
- 如果某段逻辑是“看到哪种代码模式就产出 finding”，放在 Rule Engine。
- 如果某段逻辑是“这个脚本能不能跑、是否需要人工审批”，放在 Filter。
- 如果某段逻辑是“命令怎么在受控环境中运行并记录结果”，放在 Sandbox。
- 如果某段逻辑是“结果如何写入 SQLite 并可按 task id 查询”，放在 Storage。
- 如果某段逻辑是“如何生成 review_report.json / review_report.md”，放在 Report。
- 如果某段逻辑是“如何避免敏感信息出现在报告和数据库中”，放在 Redactor。

### 5.11.4 常见误区

以下做法应避免：

- 不要把所有规则说明和脚本调度都塞进 `agent.py`。
- 不要让 Skill 直接负责数据库写入和全局报告拼装。
- 不要让 Rule Engine 直接决定高风险脚本是否执行。
- 不要把脱敏逻辑只放在报告生成最后一步，必须在入库前也执行。
- 不要在 Storage 层夹带业务判断，Storage 只负责持久化。
- 不要在 Report 层重新做规则识别，Report 只消费结构化结果。

## 6. 端到端执行链路

```text
CLI / fixture input
  -> 输入加载
  -> diff 解析
  -> 规则引擎初筛
  -> Filter 策略决策
  -> 允许的任务进入 Container / Cube 沙箱执行
  -> 汇总 findings / warnings / needs_human_review
  -> 敏感信息脱敏
  -> 写入 SQLite
  -> 输出 review_report.json / review_report.md
```

## 7. 分阶段实施计划

## Phase 1：类型定义与主链路骨架

### 目标

建立统一的数据模型和主流程骨架，让项目从“目录已创建”进入“可开始串联逻辑”的状态。

### 任务

- 完成 `src/review_types.py`
- 定义核心对象：`ReviewTask`、`ReviewInput`、`ParsedDiff`、`ReviewFinding`、`SandboxRunRecord`、`FilterDecisionRecord`、`ReviewReport`
- 补齐 `run_agent.py` 的 CLI 参数定义
- 在 `agent/agent.py` 中确定主流程函数签名

### 产出

- 统一类型模型
- 可执行但仍为占位实现的 CLI 入口
- 主流程函数框架

### 验收点

- 代码结构固定下来
- 后续模块可以围绕统一类型并行开发

## Phase 2：输入解析与规则 MVP

### 目标

先具备“读输入 + 找问题”的能力，优先完成最核心的确定性评审逻辑

### 任务

- 实现 `--diff-file`、`--repo-path`、fixture 输入适配
- 实现 unified diff 解析
- 实现 6 类规则的第一版
- 产出结构化 findings
- 实现初版去重和置信度分流

### 产出

- `src/input_loader.py`
- `src/diff_parser.py`
- `src/rule_engine.py`
- `src/deduper.py`

### 验收点

- 公开样例中的静态风险能够被初步识别
- finding 结构字段齐全
- 同类同位置问题不重复报

## Phase 3：数据库与报告

### 目标

补齐“可落库、可查询、可交付”的能力。

### 任务

- 定义 SQLite schema
- 实现 repository 层
- 实现 task、finding、sandbox run、report 的落库
- 生成 `review_report.json`
- 生成 `review_report.md`

### 产出

- `src/storage/models.py`
- `src/storage/repository.py`
- `src/storage/init_db.py`
- `src/report_writer.py`

### 验收点

- 可通过 `task_id` 查到整条评审链路
- 报告字段和 issue 要求一致

## Phase 4：沙箱和 Filter 治理

### 目标

让系统从“静态规则扫描器”升级为“具备受控执行能力的 Agent”。

### 任务

- 接入 Container runtime 作为默认生产方案
- 预留 Cube/E2B 接入点
- 实现 Local fallback
- 将 lint、test、解析脚本纳入 `skill_run`
- 实现 Filter 前置拦截
- 对 timeout、输出截断、失败日志做规范化记录

### 产出

- `agent/tools.py`
- `src/filter_policy.py`
- 经过 skill 驱动的脚本执行链路

### 验收点

- `deny / needs_human_review` 不会进入沙箱执行
- 超时或失败不会导致评审整体崩溃

## Phase 5：脱敏、dry-run 和 fixture 完整覆盖

### 目标

补齐真正影响验收的可靠性和可测试性要求。

### 任务

- 实现统一脱敏逻辑
- 为 dry-run / fake model 模式提供无模型执行路径
- 填充 8 条公开 fixture
- 将 fixture 跑通并生成报告

### 产出

- `src/redactor.py`
- 完整 fixtures
- 稳定的 dry-run 模式

### 验收点

- 8 条公开样本全部可运行
- 报告和数据库中不出现敏感信息明文
- dry-run 模式两分钟内跑完

## Phase 6：验收收口与质量门禁
### 目标

对照 issue 的验收标准做最后收口。

### 任务

- 校对报告内容完整性
- 复查数据库字段完整性
- 复查监控指标齐全性
- 复查 timeout、输出限制、Filter 决策链
- 复查高危问题检出率和误报率控制思路

### 验收点

- 所有显式验收项在设计和代码层面均有对应实现
- README、示例输出和方案设计说明齐备

## 8. 测试计划

### 8.1 公开 fixture 清单

必须至少包含以下 8 类：

- 无问题 diff
- 安全问题
- 异步资源泄漏
- 数据库连接生命周期问题
- 测试缺失
- 重复 finding
- 沙箱执行失败
- 敏感信息脱敏

### 8.2 单元测试重点

- diff 解析正确性
- 规则命中准确性
- finding 去重逻辑
- 脱敏逻辑
- Filter 决策逻辑
- repository 持久化逻辑

### 8.3 集成测试重点

- dry-run 模式端到端执行
- 沙箱失败后的兜底行为
- 报告生成内容完整性
- `task_id` 查询链路完整性

## 9. 验收标准映射

### 标准 1：8 条样本必须全部可运行并生成报告

- 通过 fixtures 和集成测试覆盖
- 报告输出路径固定为 `review_report.json` 和 `review_report.md`

### 标准 2：隐藏样本高危问题检出率 >= 80%，误报率 <= 15%

- 采用确定性高危规则优先策略
- 对低置信问题降级到 `warnings` 或 `needs_human_review`
- 优先控制高危类别的 precision 和 recall

### 标准 3：数据库完整记录 task、sandbox run、finding 和 report

- 通过 `review_tasks`、`sandbox_runs`、`findings`、`review_reports` 等表保证
- 提供按 `task_id` 查询的 repository 接口

### 标准 4：沙箱具备超时和输出限制，失败不崩

- 运行层统一设置 timeout
- 对 stdout/stderr 做大小限制和摘要化
- 用错误记录替代异常崩溃

### 标准 5：敏感信息脱敏检出率 >= 95%

- 对所有对外输出和入库内容统一走 `redactor`
- 在测试中覆盖 API Key、token、password 等模式

### 标准 6：dry-run / fake model 模式 <= 2 分钟

- dry-run 不依赖真实模型
- 规则识别和脚本执行保持轻量
- fixtures 优先使用小规模输入

### 标准 7：高风险脚本必须经过 Filter 决策

- 所有沙箱执行统一从 Filter policy 入口进入
- `deny / needs_human_review` 明确阻断执行

### 标准 8：报告内容完整

- 在 `report_writer.py` 中固定输出以下 sections：
  - findings 摘要
  - 严重级别统计
  - 人工复核项
  - Filter 拦截摘要
  - 监控指标
  - 沙箱执行摘要
  - 可执行修复建议

## 10. 风险与应对

### 风险 1：规则过于宽泛导致误报率升高

- 优先实现高危类别的高精度规则
- 低置信度问题不直接进入高置信 findings

### 风险 2：沙箱能力依赖环境，导致本地开发不稳定

- 提供 Container 默认方案和 Local fallback
- dry-run 模式避免强依赖真实外部环境

### 风险 3：脱敏遗漏导致验收失败

- 将脱敏逻辑前置到“报告生成前”和“数据库写入前”
- 为脱敏单独写单元测试

### 风险 4：数据库 schema 过于简化，后续难以满足查询和审计

- 按 task、filter、sandbox、finding、report 五类记录拆表
- 保留 JSON summary 字段和可扩展字段

## 11. README 与附加交付物计划

除代码实现外，还需要同步补齐：

- 示例 README
- 示例 `review_report.json`
- 示例 `review_report.md`
- 一份 300-500 字方案设计说明，解释：
  - Skill 设计
  - 沙箱隔离策略
  - Filter 策略
  - 监控字段
  - 数据库 schema
  - 去重降噪
  - 安全边界

## 12. 当前建议的实施顺序

建议严格按以下顺序推进：

1. `review_types.py`
2. `input_loader.py`
3. `diff_parser.py`
4. `rule_engine.py`
5. `deduper.py`
6. `storage/models.py`
7. `storage/repository.py`
8. `report_writer.py`
9. `filter_policy.py`
10. `redactor.py`
11. `agent/tools.py`
12. `run_agent.py`
13. fixtures 和测试收口

## 13. 下一步动作

当前最优先的三个实现项是：

1. 定义统一类型模型，固定主数据结构。
2. 完成输入解析和 diff 解析，打通“读输入 -> 得到候选行”的链路。
3. 完成第一版规则引擎，先让系统具备稳定产出 findings 的能力。

## 14. 第一期完成线

第一期的目标不是把系统做成最终形态，而是交付一个满足 issue 显式要求、可稳定运行、可验证链路完整的 MVP。第一期完成的标准如下：

### 14.1 功能完成线

- 支持 `--diff-file`、`--repo-path` 和 fixture 三种输入方式
- 提供 `code-review` Skill，包含 `SKILL.md`、规则文档和脚本目录
- 支持通过 Container 或 Cube/E2B 执行必要脚本，本地环境仅作为开发 fallback
- 至少覆盖以下 4 类及以上规则：
  - 安全风险
  - 异步错误
  - 资源泄漏
  - 测试缺失
  - 敏感信息泄漏
  - 数据库事务或连接生命周期问题
- 输出结构化 findings，并区分 findings、warnings、`needs_human_review`
- 将 task、sandbox run、finding、report、filter decision 写入 SQLite
- 生成 `review_report.json` 和 `review_report.md`
- 支持 `dry-run / fake model`，在无真实模型 key 时跑通全链路

### 14.2 工程完成线

- 8 条公开 diff 样本全部可以运行
- 8 条样本都能生成报告和数据库记录
- 脱敏逻辑在报告和数据库写入前统一生效
- Filter 决策链生效，`deny / needs_human_review` 不会直接进入沙箱
- 沙箱执行具备 timeout 和输出限制，失败不导致整体崩溃

### 14.3 交付完成线

- 示例目录完整
- README、开发计划、样例输出、fixture、测试和方案设计说明齐备
- 开发者能通过示例直接理解系统结构和执行链路

## 15. 后续目标

第一期完成后，后续工作不立即实现，但需要在设计上预留演进空间。后续目标按优先级分为以下几个阶段

## 15.1 第二期：质量增强

目标：在不破坏第一期链路稳定性的前提下，提高检出率、降低误报率、提升建议质量

### 计划内容

- 扩充规则库，覆盖更多真实工程中的高频缺陷模式
- 对现有规则增加上下文判断，减少“只看单行”造成的误报
- 优化 `confidence` 打分和降噪策略
- 增强 recommendation 模板，让修复建议更可执行
- 为每类规则增加更多正例、反例和边界样本

### 预期收益

- 提高隐藏样本上的高危问题检出率
- 更稳定地控制误报率
- 提升报告的可读性和实用性

## 15.2 第三期：Agent 编排增强

目标：让 Agent 从“固定流程驱动”演进为“根据任务上下文动态编排”的审查助手

### 计划内容

- 根据 diff 类型、语言或目录自动选择要执行的 skill 脚本
- 增加更细粒度的子能力，例如按语言、框架或风险类型拆分脚本
- 在需要时引入动态工具选择，只暴露当前审查任务所需工具
- 将模型能力限定在高价值场景，例如复杂问题解释、建议润色和报告总结

### 预期收益

- 减少无效沙箱执行。
- 提高不同代码场景下的适配性。
- 让 Agent 更像可控编排器，而不是固定脚本驱动器。

## 15.3 第四期：评测和回放能力

目标：把自动 CR Agent 从示例升级为可持续演化、可对比验证的评测对象。

### 计划内容

- 建立标准化评测集和评分规则。
- 支持相同输入在不同规则版本上的结果对比。
- 支持按 `task_id` 或样本集回放完整审查流程。
- 统计不同版本的检出率、误报率、耗时和失败分布。

### 预期收益

- 为规则迭代提供量化依据。
- 降低后续优化时引入回归问题的风险。
- 让该示例具备更强的研究和教学价值。

## 15.4 第五期：平台化和集成能力

目标：让该示例具备接近真实工程接入场景的扩展能力。

### 计划内容

- 抽象数据库接口，支持 SQLite 以外的 SQL 后端。
- 支持按 repo、severity、category、时间范围查询历史 review。
- 提供与 CI 或 PR 工作流集成的入口。
- 视需求增加更强的监控聚合和可视化展示。

### 预期收益

- 更容易接入实际工程流程。
- 更容易沉淀历史审查结果。
- 从“示例”平滑演进为“可集成能力模块”。

## 15.5 长期方向

长期来看，该项目可以继续演进为：

- 自动化代码评测基座
- CI 审查助手
- 风险扫描器
- 审查结果回放和回归测试工具
- 更通用的 Agent 安全执行与治理示例
