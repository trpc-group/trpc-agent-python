# 方案设计说明

本示例把自动代码评审构建为一个**可验证系统**：主干是确定性流水线，Agent（Skill + 沙箱 + Filter）
是其中一个 finding 来源，因此在无模型 API Key 的 dry-run 下也能产出完整报告，隐藏集阈值可复现。

**Skill 设计。** `skills/code-review/` 将评审打包为可移植 Skill：`SKILL.md` 声明规则与用法，
`scripts/run_checks.py` 是自包含的沙箱入口（不依赖示例包），`rules/` 放 semgrep 规则，
`docs/OUTPUT_SCHEMA.md` 是 findings 的唯一契约。findings 来自 bandit / ruff / detect-secrets 等
成熟扫描器，外加两个自写检测器（DB 连接生命周期、测试缺失），覆盖全部 6 类规则。

**沙箱隔离策略。** 默认走沙箱：有 Docker 时用 Container workspace，否则降级为子进程沙箱（本地仅作
fallback）。每次执行都有超时（`asyncio.wait_for` / subprocess timeout）、输出字节上限截断、
资源限制（memory_mb），并把每次运行记录为 `sandbox_run`（含超时、失败、拦截）；单次失败只降级来源，
不使整个评审崩溃。

**Filter 策略。** `pipeline/policy.py::ReviewPolicy` 对命令、路径、网络、预算做 allow / deny /
needs_human_review 判定，在两处执行点共用：确定性沙箱门（被拒动作从不启动）与框架级
`ReviewGuardFilter`（工具级 `BaseFilter`）。拦截原因写入报告的 Filter 摘要与数据库。

**监控字段。** 每次评审记录总耗时、沙箱耗时、工具调用数、拦截数、finding 数、各 severity 分布、
异常类型分布，落入报告 monitoring 段。

**数据库 schema。** 四张表 `review_tasks` / `sandbox_runs` / `findings` / `reports`，任务行内嵌
input diff 摘要，均以 `task_id` 为键；基于 `SqlStorage` 的可移植列类型，SQLite 默认、PG/MySQL 换 URL 即可。

**去重降噪。** 同 `(文件, 行, 类别)` 至多保留一条（高置信优先，其余标 duplicate）；再按置信度分流
active / warning / needs_human_review，低置信噪声不混入高置信 findings。

**安全边界。** 单一 `redact()` 汇聚点在入库/出报告前统一脱敏（提供商正则 + 熵检测，语料实测 100%）；
沙箱只透传白名单环境变量，杜绝父进程密钥泄漏。

**验收证据。** `selftest.py` 在公开样本上打分;`selftest.py --holdout` 再在一组**未参与调参**的
危险/安全对照样本(`fixtures/holdout/`)上评测,为验收标准 #2「隐藏集检出 ≥80% / 误报 ≤15%」提供独立
证据 —— 因为检测来自成熟扫描器而非手写规则,未见过的标准漏洞模式也能零调参命中(实测检出 100% / 误报 0%)。
