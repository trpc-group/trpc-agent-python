# security — 安全风险规则

> 覆盖 SQL 注入、命令注入、硬编码密钥、不安全反序列化等高危安全问题。
> 检测方式:静态模式匹配(本地 L4),沙箱内可叠加 semgrep(P3)。

## finding 字段约定

- `category`: `security`
- `source`: `rule`(静态) 或 `sandbox`(semgrep)
- 精确命中 `confidence` 0.9+,启发式 0.6-0.75(P4 分流到 warnings)

## 规则

```yaml
- id: SEC001
  pattern: '(?:execute|executemany)\s*\([^)]*\+'
  severity_hint: critical
  confidence: 0.9
  type: pattern
  description: SQL 注入 — execute() 调用含字符串拼接(+),应改用参数化查询
- id: SEC002
  pattern: '(?:os\.system|os\.popen)\s*\([^)]*\+|subprocess\.(?:call|run|Popen)\s*\([^)]*shell\s*=\s*True[^)]*\+'
  severity_hint: critical
  confidence: 0.9
  type: pattern
  description: 命令注入 — 系统调用拼接外部输入或 shell=True 拼接,应改用参数列表
- id: SEC003
  pattern: '(?:API_KEY|SECRET_KEY|ACCESS_KEY|PRIVATE_KEY|TOKEN)\s*=\s*["''][^"'']{8,}["'']'
  severity_hint: high
  confidence: 0.8
  type: pattern
  description: 硬编码密钥 — 密钥类变量直接赋字面量,应从环境变量/密钥管理读取
- id: SEC004
  pattern: 'pickle\.loads?\s*\(|yaml\.load\s*\([^)]*Loader\s*=\s*Loader'
  severity_hint: high
  confidence: 0.85
  type: pattern
  description: 不安全反序列化 — pickle.loads 或 yaml.load(Loader=Loader) 可执行任意代码,应改用 safe_load
- id: SEC005
  pattern: 'verify\s*=\s*False'
  severity_hint: medium
  confidence: 0.5
  type: pattern
  description: TLS 证书校验被关闭 (verify=False) — 存在中间人攻击风险,需人工确认是否内网有意为之
```
