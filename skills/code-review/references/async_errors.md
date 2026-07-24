# 异步编程与异常处理详解（Async & Error Handling）

## 异步编程最佳实践

### 1. async/await 正确使用

**常见错误：**

```python
# ❌ 忘记 await
async def fetch_data():
    return await api_call()

result = fetch_data()  # 返回 coroutine，而非实际结果

# ❌ 在同步上下文调用 async 函数
def sync_function():
    await async_function()  # 语法错误

# ❌ async 函数中调用阻塞操作
async def bad_async():
    time.sleep(1)  # 阻塞事件循环
    return heavy_computation()  # 阻塞事件循环
```

**正确做法：**

```python
# ✅ 正确的 async/await
async def main():
    result = await fetch_data()
    print(result)

# ✅ 使用 asyncio.run
asyncio.run(main())

# ✅ 非阻塞替代
async def good_async():
    await asyncio.sleep(1)  # 非阻塞
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, heavy_computation)  # 在线程池执行
```

### 2. 并发控制

**任务组管理：**

```python
# ✅ 使用 asyncio.TaskGroup (Python 3.11+)
async def fetch_multiple():
    async with asyncio.TaskGroup() as tg:
        task1 = tg.create_task(fetch_url("url1"))
        task2 = tg.create_task(fetch_url("url2"))
    return task1.result(), task2.result()

# ✅ 兼容版本（Python 3.7-3.10）
async def fetch_multiple_compat():
    tasks = [
        asyncio.create_task(fetch_url(f"url{i}"))
        for i in range(3)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return results
```

**超时控制：**

```python
# ✅ 设置超时
try:
    result = await asyncio.wait_for(fetch_data(), timeout=5.0)
except asyncio.TimeoutError:
    logger.error("操作超时")
```

## 异常处理策略

### 1. 异常捕获范围

**不当做法：**

```python
# ❌ 过于宽泛的异常捕获
try:
    risky_operation()
except:
    pass  # 吞掉所有异常，难以调试

# ❌ 捕获所有 Exception
try:
    risky_operation()
except Exception as e:
    pass  # 同样不推荐

# ❌ 捕获后无日志记录
try:
    db.execute(query)
except sqlite3.Error:
    return None  # 静默失败
```

**正确做法：**

```python
# ✅ 精确捕获特定异常
try:
    result = int(user_input)
except ValueError as e:
    logger.error(f"无效的数字输入: {user_input}")
    raise

# ✅ 分层处理
try:
    result = api_call()
except ConnectionError as e:
    logger.warning(f"连接失败，重试中: {e}")
    return retry_call()
except TimeoutError as e:
    logger.error(f"请求超时: {e}")
    raise
except APIError as e:
    logger.error(f"API 错误: {e}")
    raise

# ✅ 捕获后记录详细信息
except Exception as e:
    logger.exception("未预期的错误")
    raise
```

### 2. 异常信息脱敏

**风险场景：**

```python
# ❌ 暴露数据库结构
except DatabaseError as e:
    return {"error": str(e)}  # 可能泄露表名、字段名

# ❌ 暴露用户信息
except Exception as e:
    return {"error": f"User {user.email} failed to login"}
```

**安全做法：**

```python
# ✅ 记录详细信息，返回通用错误
except DatabaseError as e:
    logger.error(f"Database error for user {user_id}: {e}")
    return {"error": "Database operation failed"}

# ✅ 敏感信息脱敏
except ValidationError as e:
    user_log = f"{user.email[:3]}***@{user.email.split('@')[1]}"
    logger.error(f"Validation failed for {user_log}: {e}")
```

## 资源清理

### 1. 异常安全的资源管理

**不当做法：**

```python
# ❌ 异常时资源未释放
def process_file():
    f = open("data.txt", "r")
    data = f.read()
    process(data)  # 如果这里抛异常，文件不会关闭
    f.close()

# ❌ 数据库连接泄露
def query_user(user_id):
    conn = db.connect()
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM users WHERE id={user_id}")
    # 如果 execute 失败，连接未关闭
    return cursor.fetchone()
```

**正确做法：**

```python
# ✅ 使用 with 语句
def process_file():
    with open("data.txt", "r") as f:
        data = f.read()
    # 即使 process() 抛异常，文件也会关闭
    process(data)

# ✅ 使用 try-finally
def query_user(user_id):
    conn = db.connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
        return cursor.fetchone()
    finally:
        conn.close()

# ✅ 异步上下文管理器
async def process_file_async():
    async with aiofiles.open("data.txt", "r") as f:
        data = await f.read()
    return process(data)
```

### 2. 连接池管理

```python
# ✅ 使用连接池
async def fetch_user(user_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
            return await cursor.fetchone()
    # 连接自动归还到连接池
```

## 检测规则

### AST 分析模式
```python
# 检测过于宽泛的异常捕获
if isinstance(node, ast.ExceptHandler) and node.type is None:
    report_issue("过于宽泛的异常捕获")

# 检测未关闭的文件
if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
    if node.func.id == 'open' and not in_with_statement(node):
        report_issue("文件未使用 with 语句")
```

### 正则模式
```python
error_patterns = [
    (r'except\s*:\s*$', "裸 except 语句"),
    (r'except\s+Exception\s*:\s*pass\s*$', "捕获异常后 pass"),
    (r'except\s+\w+\s*:\s*pass\s*$', "捕获异常后无处理"),
]
```

## 修复优先级
1. **High**：过于宽泛的异常捕获、资源泄露
2. **Medium**：异常信息泄露、未记录日志
3. **Low**：异常处理不够精细化

## 参考资料
- Python 异步编程官方文档
- `contextlib` 模块文档
- 异常处理最佳实践 (PEP 8)
