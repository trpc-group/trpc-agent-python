# 数据库生命周期（Database Lifecycle）

## 检查项

### 1. 事务管理
- ❌ 事务未提交/回滚
- ❌ 长时间持有事务锁
- ✅ 明确的事务边界和错误处理

### 2. 连接池使用
- ❌ 每次请求创建新连接
- ❌ 连接未归还到连接池
- ✅ 使用连接池并正确归还连接

### 3. N+1 查询问题
- ❌ 循环中执行查询
- ❌ 缺少预加载（eager loading）
- ✅ 使用 JOIN 或批量查询

### 4. 数据库连接泄露
- ❌ 异常时连接未关闭
- ❌ 游标未关闭
- ✅ 使用上下文管理器

## 示例代码

### ❌ 错误示例
```python
# 事务未提交
conn.begin()
conn.execute(update_query)
# 缺少 commit/rollback

# N+1 查询
for user in users:
    orders = conn.execute(f"SELECT * FROM orders WHERE user_id={user.id}")
    # 每个用户执行一次查询

# 连接未关闭
conn = db.connect()
cursor = conn.cursor()
cursor.execute(query)  # 异常时连接泄露
```

### ✅ 正确示例
```python
# 正确的事务管理
try:
    conn.begin()
    conn.execute(update_query)
    conn.commit()
except Exception as e:
    conn.rollback()
    raise

# 批量查询避免 N+1
user_ids = [u.id for u in users]
orders = conn.execute(f"SELECT * FROM orders WHERE user_id IN ({','.join(map(str, user_ids))})")

# 使用连接池和上下文管理器
with pool.get_connection() as conn:
    with conn.cursor() as cursor:
        cursor.execute(query)
```

## 检测方法
- AST 分析：检测循环中的 SQL 查询
- 正则匹配：`\bexecute\(`、`\bexecutemany\(` 在循环中
- 数据模型分析：检测 ORM 对象的属性访问模式
