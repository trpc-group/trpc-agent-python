# ReviewMind 方案设计说明

## 项目概述

ReviewMind 是一个基于 tRPC-Agent-Python 框架构建的自动代码评审 Agent，将代码审查流程（diff 解析、规则匹配、静态分析、结果分类、报告生成）封装为可复用、可审计、可评测的系统。核心设计理念是"Skills + 沙箱 + 数据库 + Filter 治理"四层分离，确保每层职责明确、可独立替换。

## Skill 设计

CR Skill (`skills/code-review/`) 采用三层信息模型：SKILL.md 提供技能概览和脚本使用说明，rules/ 目录存放 5 类风险规则文档（安全、异步、资源泄漏、数据库事务、测试缺失），scripts/ 目录存放可在沙箱中执行的检查脚本。Agent 通过 `skill_load` 按需加载规则、通过 `skill_run` 在隔离 workspace 中执行脚本，避免将所有规则文本注入 Prompt 导致 token 浪费。

## 沙箱隔离策略

默认生产方案使用 `ContainerCodeExecutor`（Docker），开发环境 fallback 到 `UnsafeLocalCodeExecutor`。每次执行设 30 秒超时、1MB 输出大小限制，环境变量仅允许白名单内的 `PATH`、`HOME`、`PYTHONPATH`、`WORKSPACE_DIR` 传入。超时或失败的沙箱执行不会导致整个评审任务崩溃，异常信息记录到 `sandbox_run` 表。

## Filter 策略

Filter 链按 `DENY → NEEDS_HUMAN_REVIEW → PASS` 顺序执行，包含 4 类过滤器：`HighRiskScriptFilter`（拦截 rm -rf /、fork 炸弹等危险模式）、`PathSafetyFilter`（禁止访问 /etc、/sys 等敏感路径）、`NetworkAccessFilter`（默认阻断所有网络访问）、`BudgetFilter`（限制执行次数和总时间）。任一 Filter 返回 DENY 时链立即终止，拦截原因写入 `filter_intercept` 表并纳入最终报告。

## 监控字段

每次审查记录以下指标：总耗时、沙箱执行耗时、解析耗时、Filter 耗时、工具调用次数、拦截次数、finding 数量、各 severity 分布、异常类型列表。所有指标持久化到 `monitor_summary` 表，支持按 task_id 查询。

## 数据库 Schema

5 张表：`review_task`（审查任务）、`sandbox_run`（沙箱执行记录）、`finding`（审查发现）、`filter_intercept`（Filter 拦截日志）、`monitor_summary`（监控审计摘要）。使用 SQLite 作为默认实现，`StorageABC` 抽象接口保留了切换 PostgreSQL、MySQL 等后端的空间。Finding 表通过 `dedup_key`（`file:line:category`）联合索引实现去重。

## 去重降噪

去重规则：同一文件同一行同一类别的 finding 只保留置信度最高的一条。降噪规则：`confidence=low` 的发现自动进入 `needs_human_review` 列表，不混入高置信 findings；`confidence=medium` 且 `severity=suggestion` 的发现同样需要人工复核。

## 安全边界

敏感信息脱敏通过 `SecretMasker` 实现，支持 API Key（sk-/pk-）、GitHub Token（ghp_）、AWS Access Key（AKIA）、JWT、数据库连接字符串等 12 种模式的正则匹配和替换。脱敏操作在报告生成阶段执行，确保报告和数据库记录中不出现明文敏感信息。