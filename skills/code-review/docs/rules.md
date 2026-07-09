# 代码评审规则

确定性规则覆盖以下问题类别。

- 安全风险：shell 命令注入、多行 `shell=True` 调用、函数参数/request/input/env taint 流向 shell 或 SQL sink、动态代码执行、不安全反序列化、关闭 TLS 证书校验、SQL 字符串拼接。
- 异步错误：未跟踪的 `asyncio.create_task`、已保存但未观察异常的 task、未 await 或未管理异常的 `asyncio.gather`。
- 资源泄漏：没有作用域生命周期的文件句柄或 HTTP session。
- 测试缺失：源码变更缺少对应测试 diff。
- 敏感信息泄漏：API key、bearer token、password、GitHub token、AWS access key、Slack token、JWT、高熵字面量和 private key。
- 数据库事务问题：写操作缺少显式事务，事务开始附近缺少 rollback，缺少 commit/rollback 信号。
- 数据库连接生命周期问题：直接连接、SQLAlchemy session、异步连接池 acquire 没有作用域 close/release。
- 外部 scanner：沙箱侧可选运行 bandit、ruff、detect-secrets、semgrep，并将输出规范化为 `scanner.*` rule id。

## Rule ID 映射

机器可读规则同时维护在 `../rules.json` 中，并由测试校验。

- `security`: `security.subprocess.shell-true`, `security.subprocess.multiline-shell-true`, `security.subprocess.tainted-shell`, `security.dynamic-code`, `security.unsafe-deserialization`, `security.tls-verify-false`, `security.sql-interpolation`, `security.sql-interpolated-variable`, `security.sql-tainted-execute`
- `secret_leak`: `security.secret.material`
- `async_error`: `async.untracked-create-task`, `async.stored-task-not-observed`, `async.gather-not-awaited`
- `resource_leak`: `resource.open-without-context`, `resource.session-without-close`
- `missing_tests`: `tests.changed-source-without-test`
- `db_lifecycle`: `db.connection-lifecycle`, `db.session-lifecycle`, `db.pool-acquire-release`
- `db_transaction`: `db.write-without-transaction`, `db.transaction-without-rollback`
- `scanner`: `scanner.bandit.B602` 等外部 scanner 规则 ID

高置信问题进入 findings，中等置信问题进入 warnings，低置信或策略敏感问题进入 `needs_human_review`。

`rules.json` 是机器可读配置，支持 `enabled`、`severity`、`confidence` 和 `recommendation`。新增代码可用 `# cr-agent: ignore=<rule_id>` 忽略下一行或当前行的特定规则，忽略数量会进入监控摘要。

# Code Review Rules

The deterministic rules cover the categories below.

- Security risks: shell command injection, multi-line `shell=True` calls, function/request/input/env taint reaching shell or SQL sinks, dynamic code execution, unsafe deserialization, disabled TLS verification, and SQL string interpolation.
- Asynchronous errors: untracked `asyncio.create_task`, stored tasks whose exceptions are not observed, and unmanaged `asyncio.gather`.
- Resource leaks: file handles or HTTP sessions opened without a scoped lifecycle.
- Test coverage gaps: source changes without a corresponding test diff.
- Sensitive information leakage: API keys, bearer tokens, passwords, GitHub tokens, AWS access keys, Slack tokens, JWTs, high-entropy literals, and private keys.
- Database transaction issues: writes without explicit transaction handling, transaction start without nearby rollback handling, and missing commit/rollback signals.
- Database connection lifecycle issues: direct connections, SQLAlchemy sessions, and async pool acquisitions without scoped close/release handling.
- External scanners: sandbox-side optional bandit, ruff, detect-secrets, and semgrep output normalized into `scanner.*` rule ids.

## Rule ID Map

Rules are also listed in `../rules.json` for machine-readable loading and tests.

- `security`: `security.subprocess.shell-true`, `security.subprocess.multiline-shell-true`, `security.subprocess.tainted-shell`, `security.dynamic-code`, `security.unsafe-deserialization`, `security.tls-verify-false`, `security.sql-interpolation`, `security.sql-interpolated-variable`, `security.sql-tainted-execute`
- `secret_leak`: `security.secret.material`
- `async_error`: `async.untracked-create-task`, `async.stored-task-not-observed`, `async.gather-not-awaited`
- `resource_leak`: `resource.open-without-context`, `resource.session-without-close`
- `missing_tests`: `tests.changed-source-without-test`
- `db_lifecycle`: `db.connection-lifecycle`, `db.session-lifecycle`, `db.pool-acquire-release`
- `db_transaction`: `db.write-without-transaction`, `db.transaction-without-rollback`
- `scanner`: external scanner rule ids such as `scanner.bandit.B602`

High-confidence issues become findings. Medium-confidence issues become warnings. Low-confidence or policy-sensitive cases become `needs_human_review`.

`rules.json` is the machine-readable configuration source and supports `enabled`, `severity`, `confidence`, and `recommendation`. Changed code can use `# cr-agent: ignore=<rule_id>` to suppress a specific rule on the current or next line; ignored counts are reported in monitoring.
