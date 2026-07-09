# resource_leak — 资源泄漏规则

> 覆盖未关闭文件/连接、try 无 finally。
> 检测方式:AST + 控制流(主)+ 模式匹配(fallback)。checker 会检查 add 行是否已含 `with` 前缀以降低误报。

## finding 字段约定

- `category`: `resource`
- `source`: `rule`
- `with` 缺失命中 `confidence` 0.7,try/finally 启发式 0.6

## 规则

```yaml
- id: RES001
  pattern: 'open\s*\('
  severity_hint: high
  confidence: 0.7
  type: ast
  description: 文件未用 with — open() 调用应放入 with 语句确保关闭
- id: RES002
  pattern: '\.connect\s*\('
  severity_hint: high
  confidence: 0.7
  type: ast
  description: 连接未用 with — connect() 返回的连接/游标应在 with 中或显式 close
- id: RES003
  pattern: 'try\s*:'
  severity_hint: medium
  confidence: 0.6
  type: ast
  description: try 无 finally — 资源在 try 中打开时,异常路径需 finally 释放
```
