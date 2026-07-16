# 安全规则（Security）

## 检查项

### 1. SQL 注入风险
- ❌ 字符串拼接 SQL
- ❌ 用户输入直接嵌入 SQL
- ✅ 使用参数化查询/ORM

### 2. 硬编码敏感信息
- ❌ 硬编码密钥、密码、Token
- ❌ 代码中包含生产环境凭据
- ✅ 使用环境变量/密钥管理服务

### 3. 不安全的随机数
- ❌ 使用 `random` 模块生成安全相关随机数
- ✅ 使用 `secrets` 模块

### 4. 未验证的用户输入
- ❌ 直接使用用户输入执行系统命令
- ❌ 未验证的文件路径操作
- ✅ 输入验证和沙箱执行

## 示例代码

### ❌ 错误示例
```python
# SQL 注入风险
query = f"SELECT * FROM users WHERE name='{user_input}'"

# 硬编码密钥
API_KEY = "sk-1234567890abcdef"

# 不安全的随机数
token = ''.join(random.choices(string.ascii_letters, k=32))
```

### ✅ 正确示例
```python
# 参数化查询
query = "SELECT * FROM users WHERE name=?"
cursor.execute(query, (user_input,))

# 环境变量
API_KEY = os.environ.get("API_KEY")

# 安全随机数
token = secrets.token_urlsafe(32)
```

## 检测方法
- 正则匹配：`"SELECT.*WHERE.*%s"`、`API_KEY\s*=\s*["\']`
- AST 分析：检测 `os.system`、`subprocess.call` 与用户输入的组合
