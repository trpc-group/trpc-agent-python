# Security Boundary

The code-review Agent should treat all review inputs as untrusted: diffs, file contents, tool output, model responses, and remote comments can contain malicious instructions or sensitive data.

## Default execution policy

- Use a container or Cube/E2B workspace runtime for untrusted command execution.
- Treat local execution as a development-only fallback.
- Do not execute model-generated commands directly on the host.
- Mount repository inputs read-only.
- Write generated artifacts only under controlled output directories.
- Use command timeouts and output-size limits.
- Pass only allowlisted environment variables.
- Do not pass secrets into the sandbox.

## Filter decisions

Before sandbox execution, the governance layer should classify each request as one of:

- `allow`: safe to run in the configured sandbox.
- `deny`: unsafe and must not run.
- `needs_human_review`: unclear risk or requires credentials/network access.

Reasons should be recorded in the review report and database.

## Sensitive data redaction

Reports, logs, stdout/stderr excerpts, and SQL rows should redact likely secrets, including:

- API keys and tokens.
- Passwords and private keys.
- Authorization headers and cookies.
- Database connection strings.
- Cloud provider credentials.

Use placeholders such as `<REDACTED_TOKEN>` instead of storing raw secret values.

## Failure behavior

Sandbox timeout, denied commands, parse failures, and storage failures should be reported as audit events.

They should not crash the full review task unless the failure prevents a safe report from being generated.

---

# 中文说明

# 安全边界

代码评审 Agent 应把所有评审输入都视为不可信数据：diff、文件内容、工具输出、模型响应和远程评论都可能包含恶意指令或敏感信息。

## 默认执行策略

- 使用 Container 或 Cube/E2B workspace runtime 执行不可信命令。
- 只把本地执行作为开发环境 fallback。
- 不要在宿主机直接执行模型生成的命令。
- 以只读方式挂载仓库输入。
- 生成的产物只能写入受控输出目录。
- 设置命令超时和输出大小限制。
- 只传入白名单环境变量。
- 不要把 secrets 传入沙箱。

## Filter 决策

在沙箱执行之前，治理层应把每个请求分类为：

- `allow`：可以在配置好的沙箱中安全执行。
- `deny`：不安全，禁止执行。
- `needs_human_review`：风险不明确，或需要凭证 / 网络访问，必须人工确认。

决策原因应记录到评审报告和数据库中。

## 敏感信息脱敏

报告、日志、stdout/stderr 摘要和 SQL 记录都应脱敏可能的 secrets，包括：

- API key 和 token。
- password 和 private key。
- Authorization header 和 cookie。
- 数据库连接字符串。
- 云厂商凭证。

用 `<REDACTED_TOKEN>` 这类占位符替代原始 secret 值。

## 失败行为

沙箱超时、命令被拒绝、解析失败和存储失败都应该作为 audit event 记录下来。

除非失败导致无法安全生成报告，否则这些错误不应让整个评审任务崩溃。
