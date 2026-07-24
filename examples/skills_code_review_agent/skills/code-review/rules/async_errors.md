# 异步错误规则

## 概述

检测异步代码中常见的错误模式，包括未处理的异步异常、协程泄漏、事件循环阻塞和资源管理不当。

## 规则列表

### AE-01: 异步上下文资源未关闭

**严重级别**: Warning

**描述**: 使用 `aiohttp.ClientSession`、`asyncpg.Connection` 等异步资源时未使用 `async with` 管理生命周期。

**检测模式**:
- `session = aiohttp.ClientSession()` 后未使用 `async with`
- 在异步函数中打开连接后未关闭
- 未使用 `try/finally` 确保资源释放

**修复建议**:
```python
# 错误
session = aiohttp.ClientSession()
resp = await session.get(url)

# 正确
async with aiohttp.ClientSession() as session:
    async with session.get(url) as resp:
        return await resp.json()
```

### AE-02: 阻塞调用在异步代码中

**严重级别**: Warning

**描述**: 在异步代码中使用 `time.sleep()` 等阻塞调用，会导致事件循环阻塞。

**检测模式**:
- `time.sleep(n)` — 阻塞调用
- `requests.get()` — 同步 HTTP 请求
- 同步文件 I/O 操作

**修复建议**:
```python
# 错误
time.sleep(1)

# 正确
await asyncio.sleep(1)
```

### AE-03: 未处理的异步异常

**严重级别**: Warning

**描述**: `asyncio.gather()` 等并发调用未处理异常，可能导致任务静默失败。

**检测模式**:
- `asyncio.gather(tasks)` 未使用 `return_exceptions=True`
- 未捕获 `asyncio.TimeoutError`
- 未处理协程中的异常

**修复建议**:
```python
# 错误
results = await asyncio.gather(*tasks)

# 正确
results = await asyncio.gather(*tasks, return_exceptions=True)
for r in results:
    if isinstance(r, Exception):
        handle_error(r)
```

### AE-04: 协程泄漏

**严重级别**: Warning

**描述**: 创建了协程对象但未 await 执行，导致协程泄漏。

**检测模式**:
- 调用异步函数但未使用 `await`
- 创建 `Task` 未跟踪其完成状态
- 未使用 `asyncio.create_task()` 的返回值