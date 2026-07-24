# 编码规范与最佳实践 — Code Review Agent 知识库

本文档是 ReviewMind 代码审查助手的参考知识库，用于 RAG 检索增强。
包含 Python 项目的编码规范、安全最佳实践和常见陷阱。

## 1. 安全编码规范

### 1.1 SQL 注入防护
- 永远使用参数化查询，不要拼接 SQL 字符串
- 正确：`cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))`
- 错误：`cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")`
- 使用 ORM 时注意 raw SQL 的传入方式

### 1.2 命令注入防护
- 避免使用 `os.system()`、`subprocess.call(shell=True)` 等
- 使用 `subprocess.run()` 并传递列表参数而非字符串
- 正确：`subprocess.run(["ls", "-l", safe_path])`
- 错误：`os.system(f"ls -l {user_input}")`

### 1.3 路径遍历防护
- 使用 `os.path.abspath()` 和 `os.path.realpath()` 规范化路径
- 检查路径是否在允许的基目录内
- 不要直接使用用户输入作为文件路径

### 1.4 敏感信息管理
- 禁止硬编码 API Key、Token、密码
- 使用环境变量或密钥管理服务
- 日志中不得输出敏感信息

## 2. 异步编程规范

### 2.1 资源管理
- 使用 `async with` 管理异步上下文资源
- 确保 `aiohttp.ClientSession`、`asyncpg.Connection` 等正确关闭
- 正确：
  ```python
  async with aiohttp.ClientSession() as session:
      async with session.get(url) as resp:
          return await resp.json()
  ```

### 2.2 并发控制
- 使用 `asyncio.Semaphore` 控制并发数
- 避免在异步代码中使用 `time.sleep()`，使用 `asyncio.sleep()`
- 注意 `asyncio.gather()` 的异常处理

### 2.3 超时处理
- 所有网络请求应设置超时
- 使用 `asyncio.wait_for()` 或 `asyncio.timeout()`
- 为长时间运行的任务设置合理的超时阈值

## 3. 数据库操作规范

### 3.1 连接管理
- 使用连接池管理数据库连接
- 确保连接在使用后正确归还给连接池
- 避免在事务中执行长时间操作

### 3.2 事务管理
- 显式使用 BEGIN/COMMIT/ROLLBACK
- 使用上下文管理器自动管理事务
- 正确：
  ```python
  async with conn.transaction():
      await conn.execute("INSERT INTO ...")
  ```

### 3.3 连接泄漏检测
- 检查 `connection.close()` 或连接池的 `release()` 调用
- 注意异常路径中的连接释放
- 使用 `try/finally` 确保连接释放

## 4. 资源管理规范

### 4.1 文件句柄
- 使用 `with open()` 上下文管理器
- 确保文件在异常时也能关闭
- 避免在循环中频繁打开/关闭文件

### 4.2 内存管理
- 处理大文件时使用流式读取
- 避免在内存中保留大量数据
- 使用生成器处理大数据集

### 4.3 网络资源
- 关闭 HTTP 连接、WebSocket 连接
- 注册清理函数处理资源释放
- 使用 `finally` 块确保资源释放

## 5. 测试规范

### 5.1 测试覆盖
- 新功能必须包含单元测试
- 修复 bug 时添加回归测试
- 测试应覆盖正常路径和异常路径

### 5.2 测试质量
- 测试应独立可重复
- 避免测试之间的依赖
- 使用 mock 替代外部依赖

### 5.3 测试命名
- 测试函数命名：`test_<功能>_<场景>_<预期>`
- 示例：`test_login_with_valid_credentials_returns_token`