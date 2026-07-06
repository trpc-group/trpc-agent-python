# 异步错误 Async Errors (`async_error`)

事件循环阻塞、丢失的协程与被丢弃的任务。
Event-loop blocking, lost coroutines and discarded tasks.

| Rule | Trigger 触发条件 | Severity | Confidence |
|---|---|---|---|
| ASY001 | `time.sleep(` inside `async def` | high | 0.90 |
| ASY002 | `requests.*` / `urllib.request.*` inside `async def` 阻塞 IO | medium | 0.75 |
| ASY003 | `asyncio.create_task(...)` 返回值被丢弃 | medium | 0.60 |
| ASY004 | 已知协程调用缺少 `await`（启发式） | high | 0.60 |
| ASY005 | `subprocess.run/call/...` inside `async def` | medium | 0.70 |

`async def` 归属通过全文件内容（可用时）或 hunk 可见行的缩进回溯判断；
纯 diff 输入下这是启发式，低置信度结果会进入人工复核桶。
Enclosing-`async def` detection walks indentation backwards over full file
content when available, else over visible hunk lines — on pure diffs this is a
heuristic, so low-confidence hits are routed to needs_human_review.

## 修复建议 Remediation

- `await asyncio.sleep(...)` instead of `time.sleep(...)`.
- Async HTTP clients (aiohttp / `httpx.AsyncClient`) or `asyncio.to_thread(...)`.
- Keep task references and `await`/`gather` them; discarded tasks may be
  garbage-collected mid-flight and silently swallow exceptions.
