# 方案设计

本示例以 `code-review` Skill 封装 `SKILL.md`、规则、脚本和 Filter 配置。Agent 可读取 diff、patch、git 工作区、文件列表或 fixture，解析变更文件、hunk、上下文和行号。主审查流水线会审计 Skill 文件哈希并加载规则；同时提供 `--skill-smoke` 和测试用例，真实调用 SDK 的 `skill_load(skill_name="code-review")` 与 `skill_run` 执行 `scripts/diff_summary.py`，证明该 Skill 能被 tRPC-Agent 原生工具链加载和运行。无模型 Key 时使用确定性规则、AST/taint 辅助和 fake sandbox 完成 dry-run；生产接入 Container 或 Cube/E2B workspace，`local` 仅作开发 fallback。

规则覆盖安全、异步错误、资源泄漏、测试缺失、敏感信息、数据库事务和连接生命周期。沙箱请求先经过 Filter，拦截高风险命令、敏感路径、非白名单网络和超预算执行；`deny` 与 `needs_human_review` 写入报告和数据库，不能执行。默认 `python:3-slim` 验证容器隔离链路，`Dockerfile.scanners` 提供带 `bandit`、`ruff`、`detect-secrets` 的镜像，用于实际离线 scanner 执行。SQLite 保存 task、输入、sandbox run、Filter 决策、finding、监控和报告，并通过 `ReviewStore` 保留 SQL 后端扩展点。报告输出 JSON/Markdown，高置信进入 findings，中置信进入 warnings，低置信进入人工复核；同一文件、行号、类别只保留最高优先级项。所有 diff、stdout、stderr、Filter reason 和 finding evidence 在落库前脱敏，并记录耗时、工具调用、拦截、异常、严重级别分布和去重数量，便于后续回放和审计。
