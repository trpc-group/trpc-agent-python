# 资源泄漏详解（Resource Leak）

## 概述
资源泄漏是指程序获取资源（文件、连接、内存等）后未正确释放，导致系统资源耗尽。

## 常见资源泄漏场景

### 1. 文件句柄泄漏

**问题代码：**

```python
# ❌ 异常时文件未关闭
def read_config():
    f = open("config.json", "r")
    data = json.load(f)  # 如果 JSON 解析失败，文件不会关闭
    f.close()
    return data

# ❌ 多次打开文件未关闭
def process_files():
    files = []
    for name in file_names:
        f = open(name, "r")  # 每个文件都未关闭
        files.append(f)
    # 函数结束后，所有文件句柄都泄露
```

**正确做法：**

```python
# ✅ 使用 with 语句
def read_config():
    with open("config.json", "r") as f:
        return json.load(f)
    # 异常时也会自动关闭

# ✅ 批量处理时及时关闭
def process_files():
    results = []
    for name in file_names:
        with open(name, "r") as f:
            results.append(f.read())
    return results
```

### 2. 数据库连接泄漏

**问题代码：**

```python
# ❌ 连接未关闭
def get_user(user_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
    return cursor.fetchone()

# ❌ 异常时连接泄露
def update_user(user_id, data):
    conn = pool.get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET name=? WHERE id=?", (data["name"], user_id))
    conn.commit()  # 如果 commit 失败，连接未归还到连接池
```

**正确做法：**

```python
# ✅ 使用上下文管理器
def get_user(user_id):
    with sqlite3.connect("database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
        return cursor.fetchone()

# ✅ 确保连接归还
def update_user(user_id, data):
    with pool.get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE users SET name=? WHERE id=?", (data["name"], user_id))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise
```

### 3. 网络连接泄漏

**问题代码：**

```python
# ❌ HTTP 连接未关闭
def fetch_data():
    response = urllib.request.urlopen("https://api.example.com/data")
    return response.read()

# ❌ Socket 未关闭
def client_handler():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("localhost", 8080))
    sock.send(request)
    # 如果后续操作失败，socket 未关闭
```

**正确做法：**

```python
# ✅ 使用 with 语句（Python 3.x）
def fetch_data():
    with urllib.request.urlopen("https://api.example.com/data") as response:
        return response.read()

# ✅ 确保关闭 socket
def client_handler():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect(("localhost", 8080))
        sock.send(request)
        return sock.recv(1024)
    finally:
        sock.close()
```

### 4. 临时文件清理

**问题代码：**

```python
# ❌ 临时文件未清理
def process_large_data(data):
    temp_path = f"/tmp/data_{time.time()}.tmp"
    with open(temp_path, "w") as f:
        f.write(data)
    process_file(temp_path)
    # 函数结束后，临时文件仍然存在
```

**正确做法：**

```python
# ✅ 使用 tempfile 自动清理
def process_large_data(data):
    with tempfile.NamedTemporaryFile(mode="w", delete=True) as f:
        f.write(data)
        f.flush()
        return process_file(f.name)
    # 退出 with 块后，文件自动删除

# ✅ 显式清理
def process_large_data(data):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            temp_path = f.name
            f.write(data)
        return process_file(temp_path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
```

## 检测方法

### AST 分析

```python
class ResourceLeakDetector(ast.NodeVisitor):
    def __init__(self):
        self.issues = []

    def visit_Call(self, node):
        # 检测 open() 调用
        if isinstance(node.func, ast.Name) and node.func.id == 'open':
            if not self.is_in_with_statement(node):
                self.issues.append({
                    "line": node.lineno,
                    "message": "open() 应使用 with 语句"
                })

        # 检测数据库连接
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in ('connect', 'get_connection'):
                if not self.is_in_with_statement(node):
                    self.issues.append({
                        "line": node.lineno,
                        "message": "数据库连接应使用上下文管理器"
                    })

        self.generic_visit(node)

    def is_in_with_statement(self, node):
        # 检查节点是否在 with 语句中
        # 实现需要遍历父节点
        return False
```

### 正则匹配

```python
leak_patterns = [
    (r'\bopen\s*\([^)]+\)\s*(?!\s+as\s+)', "open() 未使用 with 语句"),
    (r'\.connect\s*\([^)]+\)\s*(?!\s+as\s+)', "数据库连接未使用 with 语句"),
    (r'socket\.socket\s*\([^)]+\)\s*(?!\s+with\s+)', "socket 未使用 with 语句"),
]
```

## 修复优先级

1. **Critical**：高频率循环中的资源泄漏
2. **High**：长时间运行的服务中的资源泄漏
3. **Medium**：用户交互功能中的资源泄漏
4. **Low**：罕见路径或短期程序中的资源泄漏

## 监控与检测

### 运行时检测

```python
# 使用 tracemalloc 检测内存泄漏
import tracemalloc
tracemalloc.start()

# 运行程序
snapshot1 = tracemalloc.take_snapshot()
# ... 执行操作 ...
snapshot2 = tracemalloc.take_snapshot()

# 比较快照
top_stats = snapshot2.compare_to(snapshot1, 'lineno')
for stat in top_stats[:10]:
    print(stat)
```

### 系统监控

```bash
# 检查文件描述符数量
lsof -p <pid> | wc -l

# 检查网络连接数
netstat -an | grep <pid> | wc -l

# 检查内存使用
ps -o pid,vsz,rss,cmd -p <pid>
```

## 最佳实践

1. **优先使用上下文管理器**：`with` 语句确保资源释放
2. **异常安全**：使用 `try-finally` 确保清理代码执行
3. **RAII 原则**：获取资源即初始化（Resource Acquisition Is Initialization）
4. **定期审查**：对长时间运行的服务进行资源使用监控

## 参考资料
- Python Context Managers
- Resource Management in Python
- `weakref` 和 `gc` 模块文档
