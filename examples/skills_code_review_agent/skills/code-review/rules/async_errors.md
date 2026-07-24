# async_errors — 异步错误规则

> 覆盖未 await 的协程、未处理 rejection、async 资源泄漏。
> 检测方式:AST 解析(主)+ 模式匹配(fallback)。diff add 行常不完整,AST 为 best-effort。

## finding 字段约定

- `category`: `async`
- `source`: `rule`
- AST 确认 `confidence` 0.8+,模式启发式 0.6-0.7(P4 分流到 warnings)

## 规则

```yaml
- id: ASY001
  pattern: '\.create_task\s*\(|asyncio\.gather\s*\('
  severity_hint: high
  confidence: 0.65
  type: ast
  description: 疑似未 await 的协程任务 — create_task/gather 返回值需 await 或存入 task
- id: ASY002
  pattern: 'aiohttp\.ClientSession\s*\(|httpx\.AsyncClient\s*\('
  severity_hint: high
  confidence: 0.65
  type: ast
  description: async 资源未 async with — ClientSession/AsyncClient 应在 async with 中使用
- id: ASY003
  pattern: 'async\s+def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\('
  severity_hint: medium
  confidence: 0.6
  type: ast
  description: async 函数定义 — checker 据此检测同 diff 内对该函数的裸调用(未 await)
```
