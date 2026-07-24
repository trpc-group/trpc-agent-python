# 数据库连接生命周期规则

## 概述

检测数据库连接管理中的常见问题，包括连接未关闭、事务未正确管理、连接池耗尽和 SQL 注入风险。

## 规则列表

### DB-01: 数据库连接未关闭

**严重级别**: Warning

**描述**: 数据库连接在使用后未关闭，可能导致连接泄漏和连接池耗尽。

**检测模式**:
- `sqlite3.connect()` 后无 `conn.close()`
- `psycopg2.connect()` 后无 `conn.close()`
- 使用连接池后未归还连接

**修复建议**:
```python
# 错误
conn = sqlite3.connect("app.db")
cursor = conn.cursor()
cursor.execute("SELECT * FROM users")
return cursor.fetchall()

# 正确
with sqlite3.connect("app.db") as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    return cursor.fetchall()
```

### DB-02: 事务未提交或回滚

**严重级别**: Warning

**描述**: 数据库事务未显式提交或回滚，可能导致数据不一致。

**检测模式**:
- 执行 INSERT/UPDATE/DELETE 后未调用 `commit()`
- 异常路径中未调用 `rollback()`
- 使用 `autocommit=False` 但未管理事务

**修复建议**:
```python
# 错误
conn.execute("INSERT INTO users (name) VALUES (?)", (name,))

# 正确
conn.execute("INSERT INTO users (name) VALUES (?)", (name,))
conn.commit()
```

### DB-03: 连接池耗尽

**严重级别**: Warning

**描述**: 未正确归还连接池中的连接，导致连接池耗尽。

**检测模式**:
- 从连接池获取连接后未调用 `release()`
- 在长时间操作中持有连接
- 连接池大小设置不合理

### DB-04: 原始 SQL 注入

**严重级别**: Critical

**描述**: 使用拼接字符串构造 SQL 查询，可能导致 SQL 注入攻击。

**检测模式**:
- `f"SELECT * FROM {table}"` — 拼接表名或字段名
- `"WHERE id = " + user_input` — 拼接用户输入
- ORM 的 raw SQL 调用未使用参数化查询

**修复建议**:
```python
# 错误
conn.execute(f"SELECT * FROM users WHERE id = {user_id}")

# 正确
conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
```