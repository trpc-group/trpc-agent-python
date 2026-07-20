# 方案设计说明

本方案将自动代码评审拆为“主流程编排 + 可复用 Skill + 受控执行 + 结构化落库”四层。主流程由 `agent/agent.py` 负责，统一接收 diff、repo path 或 fixture，完成输入归一化、diff 解析、规则执行、Filter 决策、skill 脚本调度、报告生成和 SQLite 持久化。`skills/code-review/` 则承载正式的 `code-review` Skill，包括 `SKILL.md`、规则文档、使用文档、脚本契约与三个确定性脚本，用于承接可复用的评审知识与脚本执行面。

沙箱隔离策略采用“统一 workspace runtime 执行 + 本地回退 + 容器优先扩展”的实现方式。当前示例中的 `local` 与 `container` 都通过同一套 workspace manager / filesystem / program runner 链路执行脚本，不再把 `container` 名义上的运行时回退到宿主 `subprocess`。其中 `local` 仍用于开发调试，`container` 在具备 Docker 环境时提供真实隔离；`cube`、`e2b` 等远端 runtime 仍保留后续接入点。统一脚本执行层继续提供 timeout、输出截断、失败记录和 Filter 前置治理，确保高风险脚本、禁止路径、默认网络访问和超预算输入不能直接进入执行链路。对脚本失败或超时，系统不会整体崩溃，而是转换为可追踪的 `sandbox_runs` 记录和结构化 finding。

数据库 schema 采用最小可查询设计，包含 `review_tasks`、`review_inputs`、`filter_decisions`、`sandbox_runs`、`findings` 和 `review_reports` 六张表，支持按 `task_id` 查询完整审查链路。报告输出同时生成 JSON 与 Markdown，两者都包含 findings 摘要、人工复核项、Filter 摘要、sandbox 摘要和监控指标。监控字段聚合总耗时、severity/category 分布、拦截次数和 sandbox 次数，便于回放和评测。

去重与降噪通过 `deduper.py` 实现：同类同文件同位置同证据的 finding 会被合并，低置信结果自动降级到 `needs_human_review` 或 `warning`。安全边界通过统一 `redactor.py` 落实，确保 API key、token、password、Bearer token 和私钥内容在报告与数据库中不出现明文。整体设计优先满足验收中的可验证性、可运行性、可审计性和 dry-run 可用性，为后续原生 `skill_run` 深化接入和 PR 收口保留清晰扩展点。
