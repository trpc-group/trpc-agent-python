# missing_tests — 缺失测试规则

> 覆盖新增公开函数无对应测试的检测。
> 检测方式:diff 关联分析 — 从 add 行提取新增顶层 `def`(非 `_` 前缀),在同 diff 的测试文件中搜索 `test_<fn>` 或 `<fn>(` 调用,未命中 → finding。

## finding 字段约定

- `category`: `tests`
- `source`: `rule`
- `confidence` 0.6(需人工确认是否真需测试),`severity_hint` low → P4 分流到 warnings

## 规则

```yaml
- id: TST001
  pattern: 'def\s+([a-zA-Z][a-zA-Z0-9_]*)\s*\('
  severity_hint: low
  confidence: 0.6
  type: diff
  description: 新增公开函数无对应测试 — 提取 add 行的新 def 名,在测试文件中找 test_<fn>
- id: TST002
  pattern: 'class\s+([A-Z][a-zA-Z0-9_]*)\s*(?:\(|:)'
  severity_hint: low
  confidence: 0.55
  type: diff
  description: 新增公开类无对应测试 — 提取 add 行的新 class 名,在测试文件中找 Test<cls>
- id: TST003
  pattern: '\.route\s*\(|@app\.(?:get|post|put|delete|route)'
  severity_hint: low
  confidence: 0.5
  type: diff
  description: 新增路由/接口端点无测试 — API 端点应有对应集成测试
```
