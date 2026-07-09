# 方案设计说明（CR Agent）

## Skill 设计
`skills/code-review` 是一个标准 tRPC-Agent Skill：`SKILL.md` 声明 6 类规则（`security`、`async_errors`、`resource_leak`、`missing_tests`、`sensitive_info`、`db_lifecycle`，已覆盖要求中 6 类问题里的 6 类）与 4 个脚本（`parse_diff` / `run_checks` / `dedupe` / `mask_secrets`）。规则正文为 Markdown 说明，检查逻辑由 `run_checks.py` 基于正则 + AST 实现，纯确定性、可离线、可复现。

## 沙箱隔离策略
沙箱执行通过 SDK `WorkspaceRuntime` 实现。运行时按 `SKILL.md` 的 `default_runtime`/`fallback` 选择：`ContainerRuntime`（Docker 容器）为默认生产方案，`CubeRuntime`（Cube/E2B 远程）为可选项，`LocalRuntime` 仅作开发 fallback，绝不设为默认。选择失败透明回退并记录真实落地后端。超时 30s、输出上限 1MB、env 白名单（`PATH`/`HOME`/`LANG`）、stdout/stderr 落地前脱敏，五道边界由 `sandbox/policy.py` 强制套用、不可绕过。

## Filter 策略
`filters/governance.py` 在脚本进入沙箱前做四类前置拦截：高风险命令（`rm -rf`/`sudo`/`eval`/`os.system`+拼接等）、禁止路径（`/etc`、`~/.ssh`、`/proc` 等）、非白名单网络访问、超预算（`>60s` 或 `>1024MB`）。`deny`/`needs_human_review` 不进入沙箱，拦截原因写入 `filter_block` 表与报告。

## 监控字段
`telemetry/tracing.py` 用 OTel 包裹各阶段，`monitor_summary` 记录：总耗时、沙箱耗时、工具调用次数、拦截次数、finding 数、各 severity 分布、异常类型分布。

## 数据库 schema
七张表围绕 `review_task`（`task_id` 全局外键）：`input_diff`（变更摘要）、`sandbox_run`（执行证据）、`finding`（含 severity/category/file/line/title/evidence/recommendation/confidence/source 九字段 + bucket 分流）、`filter_block`、`monitor_summary`、`review_report`。基于 SDK `SqlStorage`（SqlAlchemy ORM），`ReviewStore` 为 Protocol，保留切换 SQL 后端空间。

## 去重降噪
`dedupe.py` 按 `(file, line, category)` 分组，同组取最高 confidence、合并多源；按置信度分流：`findings`(≥0.8) / `warnings`(0.6–0.8) / `needs_human_review`(<0.6)，低置信度绝不混入高置信 findings。

## 安全边界
除沙箱五边界外，敏感信息经 `mask_secrets`（已知格式 + 香农熵 >4.5）双重识别，报告与落库均脱敏；沙箱异常整体 `except→FakeRunner` 降级，流水线不崩溃。
