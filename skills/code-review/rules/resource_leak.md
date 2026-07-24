# 资源泄漏（Resource Leak）

## 检查项

### 1. 文件句柄泄漏
- ❌ 打开文件后未关闭
- ❌ 异常发生时文件未关闭
- ✅ 使用 `with` 语句确保关闭

### 2. 数据库连接泄漏
- ❌ 获取数据库连接后未释放
- ❌ 连接池耗尽
- ✅ 使用上下文管理器或 `try-finally`

### 3. 临时文件清理
- ❌ 创建临时文件后未删除
- ❌ 程序崩溃后临时文件残留
- ✅ 使用 `tempfile` 模块的自动清理机制

### 4. 网络连接管理
- ❌ HTTP 连接未关闭
- ❌ Socket 未关闭
- ✅ 使用连接池或上下文管理器

## 示例代码

### ❌ 错误示例
```python
# 文件未关闭
f = open("data.txt", "w")
f.write(data)  # 如果异常发生，文件不会关闭

# 数据库连接未释放
conn = db.connect()
cursor = conn.cursor()
cursor.execute(query)  # 异常时连接未关闭

# 临时文件未清理
temp_path = "/tmp/tempfile.dat"
with open(temp_path, "w") as f:
    f.write(data)
# 文件仍然存在，需要手动清理
```

### ✅ 正确示例
```python
# 使用 with 语句
with open("data.txt", "w") as f:
    f.write(data)  # 自动关闭

# 数据库连接使用上下文管理器
with db.connect() as conn:
    cursor = conn.cursor()
    cursor.execute(query)  # 自动释放连接

# 临时文件自动清理
with tempfile.NamedTemporaryFile(delete=True) as f:
    f.write(data)
    # 文件在退出 with 块后自动删除
```

## 检测方法
- AST 分析：检查 `open()`、`db.connect()` 是否在 `with` 语句中
- 正则匹配：`open\(.*\)(?!\s*with)`、`\.connect\(.*\)(?!\s*with)`
