# 沙箱策略

生产评审脚本应运行在 Container 或 Cube/E2B workspace 中。CLI 默认使用 Container；显式 `--dry-run` 或 `--sandbox fake` 使用 fake sandbox，方便 CI 和本地 dry-run 在没有 Docker、API Key 或网络访问的情况下完成全流程验证。

## 控制项

- 每个脚本都有 timeout。
- stdout/stderr 共享同一个总字节上限，超过预算会截断并终止脚本。
- 只传递环境变量白名单。
- 持久化前执行敏感信息脱敏。
- 默认禁止网络访问。
- 网络型 scanner 必须显式声明目标域名，并通过 allowlist Filter。
- pip/git/npm/ssh 等未声明可审计域名的隐式联网命令会进入人工复核。
- 高风险命令在执行前被拒绝。
- 敏感路径标记为人工复核。
- `filter_policy.json` 记录 policy-as-code，包括命令拦截、禁止路径、预算和 workspace 路径 allowlist。
- 沙箱脚本只应读取 `scripts/` 和 `work/`，写入限制在 `work/`。
- `scanner_probe.py` 只会物化安全的相对 diff 路径，并拒绝 `..`、绝对路径和越过扫描根目录的目标。
- `--repo-path` 输入会把受限仓库快照上传到 `repo/`，跳过 `.git`、`.env`、密钥目录和超预算文件。

`local` 执行仅作为显式开发 fallback，不作为生产默认方案。

# Sandbox Policy

Production review scripts should run in Container or Cube/E2B workspaces. The CLI defaults to Container; explicit `--dry-run` or `--sandbox fake` uses the fake sandbox so CI and local dry-runs do not need Docker, API keys, or network access.

## Controls

- Timeout per script.
- Combined stdout/stderr byte limit; over-budget output is truncated and the script is stopped.
- Environment variable whitelist.
- Secret redaction before persistence.
- Network deny by default.
- Network-backed scanners must declare target domains and pass the allowlist Filter.
- Implicit network commands such as pip/git/npm/ssh require human review unless they declare reviewable domains.
- High-risk commands denied before execution.
- Sensitive paths marked for human review.
- `filter_policy.json` records policy-as-code for command interception, forbidden paths, budgets, and workspace path allowlists.
- Sandbox scripts should read only `scripts/` and `work/`, with writes constrained to `work/`.
- `scanner_probe.py` materializes only safe relative diff paths and rejects `..`, absolute paths, and targets outside the scan root.
- `--repo-path` inputs stage a bounded repository snapshot under `repo/`, excluding `.git`, `.env`, secret directories, and over-budget files.

Local execution is available only as an explicit development fallback.
