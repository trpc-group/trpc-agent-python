# 安全风险 Security Risk (`security_risk`)

命令注入、SQL 注入、危险反序列化与不安全配置。
Command injection, SQL injection, dangerous deserialization and insecure settings.

| Rule | Trigger 触发条件 | Severity | Confidence |
|---|---|---|---|
| SEC001 | `os.system(...)` | high | 0.90 |
| SEC002 | `subprocess.*(..., shell=True)` | high | 0.90 |
| SEC003 | `eval(` / `exec(` on data 动态执行 | high | 0.75 |
| SEC004 | `pickle.load/loads` 反序列化不可信数据 | high | 0.85 |
| SEC005 | `yaml.load` 未用 SafeLoader | medium | 0.85 |
| SEC006 | `.execute(f"..."/"..."+ / %)` 拼接 SQL | critical | 0.85 |
| SEC007 | `md5(` 与 password 同行（弱哈希） | medium | 0.60 |
| SEC008 | `verify=False` 关闭 TLS 校验 | high | 0.90 |
| SEC009 | 命令字符串由动态输入拼接 | critical | 0.88 |

## 修复建议 Remediation

- 用参数化查询代替字符串拼接 SQL：`cursor.execute("... WHERE id = %s", (uid,))`。
- 用 `subprocess.run([...], shell=False)` + 参数列表代替 `os.system` / `shell=True`。
- Replace `eval`/`exec` with `ast.literal_eval` or an explicit dispatch table.
- Never unpickle untrusted bytes; prefer JSON with schema validation.
- Keep TLS verification on; pin an internal CA bundle when necessary.
