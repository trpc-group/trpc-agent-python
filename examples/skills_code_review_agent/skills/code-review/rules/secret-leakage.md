# 敏感信息泄漏 Secret Leakage (`secret_leakage`)

提交进源码的密钥、令牌、口令与私钥。
Keys, tokens, passwords and private keys committed to source.

检测与脱敏共用同一张正则表（`scripts/lib/secret_patterns.py`）——
沙箱规则用它**检测**，宿主端 `SecretRedactor` 用它**脱敏**，永不漂移。
Detection and redaction share ONE canonical pattern table
(`scripts/lib/secret_patterns.py`): the sandbox rule *detects* with it, the
host-side `SecretRedactor` *scrubs* with it — they can never drift apart.

| Pattern id | 覆盖 Coverage |
|---|---|
| aws_access_key_id | `AKIA/ASIA/...` + 16 chars |
| aws_secret_access_key | `aws_secret_access_key = <40 chars>` |
| github_token | `ghp_/gho_/ghu_/ghs_/ghr_...` |
| slack_token | `xox[baprs]-...` |
| openai_api_key | `sk-...` (≥20 chars) |
| google_api_key | `AIza...` (≥30 chars) |
| jwt_token | `eyJ...`.`...`.`...` |
| private_key_block | `-----BEGIN ... PRIVATE KEY-----` |
| bearer_token | `Bearer <token>` |
| url_credentials | `scheme://user:pass@host` |
| generic_assignment | `password/secret/token/api_key/... = "value"` |

误报防护：值为 `os.environ...`、`${VAR}`、`<placeholder>`、`None/True/False`
等引用形态时不报。所有 finding 的 evidence 在沙箱内即完成脱敏
（`***REDACTED***`），明文密钥不会进入 findings JSON、报告或数据库。
False-positive guard: values that are clearly references
(`os.environ...`, `${VAR}`, `<placeholder>`, `None/True/False`) are skipped.
Evidence is redacted INSIDE the sandbox — plaintext secrets never reach the
findings payload, the report or the database.

| Rule | Severity | Confidence |
|---|---|---|
| SCR_* (one per pattern id) | critical | 0.90 |

## 修复建议 Remediation

立即吊销并轮换泄漏的凭据；改从环境变量或密钥管理服务加载。
Rotate the leaked credential immediately; load it from an environment variable
or a secret manager instead.
