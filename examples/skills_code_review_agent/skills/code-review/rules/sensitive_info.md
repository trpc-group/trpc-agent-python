# sensitive_info — 敏感信息规则

> 覆盖明文 API key / token / password / 私钥 / 连接串。
> 检测方式:已知前缀正则(精确)+ Shannon 熵值(启发式),与 `mask_secrets.py` 共享同一套正则。

## finding 字段约定

- `category`: `sensitive`
- `source`: `rule`
- 已知前缀命中 `confidence` 0.95,熵值命中 0.8

## 规则

```yaml
- id: SEN001
  pattern: 'AKIA[0-9A-Z]{16}'
  severity_hint: critical
  confidence: 0.95
  type: pattern
  description: AWS Access Key ID 明文
- id: SEN002
  pattern: 'sk-[a-zA-Z0-9]{20,}'
  severity_hint: critical
  confidence: 0.95
  type: pattern
  description: OpenAI API key 明文
- id: SEN003
  pattern: 'ghp_[a-zA-Z0-9]{36}'
  severity_hint: critical
  confidence: 0.95
  type: pattern
  description: GitHub personal access token 明文
- id: SEN004
  pattern: 'password\s*=\s*["''][^"'']+["'']'
  severity_hint: critical
  confidence: 0.85
  type: pattern
  description: 明文 password 赋值
- id: SEN005
  pattern: '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  severity_hint: critical
  confidence: 0.95
  type: pattern
  description: 私钥头明文
- id: SEN006
  pattern: '(?:postgres|mysql|mongodb)://[^\s/]+:[^\s@/]+@'
  severity_hint: critical
  confidence: 0.9
  type: pattern
  description: 连接串含明文凭证
```

> 熵值检测(SEN_ENT):连续 ≥20 字符的高熵串(Shannon entropy > 4.5)且非纯英文单词,
> 疑似密钥。由 `_check_sensitive` 内置实现,`confidence` 0.8,`severity_hint` critical。
