# db_lifecycle — 数据库生命周期规则

> 覆盖连接未关闭、事务未提交/回滚、连接池泄漏。
> 检测方式:AST(主)+ 模式匹配(fallback)。

## finding 字段约定

- `category`: `db`
- `source`: `rule`
- 连接/游标未关闭 `confidence` 0.7,事务路径 0.65

## 规则

```yaml
- id: DB001
  pattern: '(?:engine|conn|connection|db)\.connect\s*\('
  severity_hint: high
  confidence: 0.7
  type: ast
  description: 数据库连接未用 with — connect() 应在 with 中或显式 close
- id: DB002
  pattern: '\.cursor\s*\('
  severity_hint: medium
  confidence: 0.65
  type: ast
  description: 游标未关闭 — cursor() 应在 with 中或显式 close
- id: DB003
  pattern: '\.begin\s*\('
  severity_hint: high
  confidence: 0.7
  type: ast
  description: 事务未提交/回滚 — begin() 后所有异常路径需 rollback,正常路径需 commit
```
