# 安全规则详解（Security Rules）

## 概述
安全规则检查旨在识别代码中可能引入安全漏洞的编程模式。

## 详细规则

### 1. SQL 注入（SQL Injection）

**原理：**
当用户输入直接嵌入 SQL 查询字符串时，攻击者可以通过构造特殊输入改变查询语义。

**检测模式：**
- 字符串拼接构建 SQL：`"SELECT * FROM users WHERE name='" + user_input + "'"`
- f-string 嵌入变量：`f"SELECT * FROM users WHERE id={user_id}"`
- % 格式化：`"SELECT * FROM users WHERE name='%s'" % user_input`

**安全做法：**
```python
# 参数化查询
cursor.execute("SELECT * FROM users WHERE name=?", (user_input,))

# ORM 使用
User.objects.filter(name=user_input)

# 查询构建器
session.query(User).filter(User.name == user_input)
```

### 2. 硬编码敏感信息（Hardcoded Secrets）

**检测范围：**
- API 密钥：`API_KEY = "sk-1234567890abcdef"`
- 数据库密码：`DB_PASSWORD = "admin123"`
- JWT 密钥：`SECRET_KEY = "my-secret-key"`
- 访问令牌：`ACCESS_TOKEN = "xyz789"`

**识别模式：**
- 变量名包含：KEY, SECRET, PASSWORD, TOKEN, CREDENTIAL
- 赋值为字符串常量（非环境变量读取）
- 正则表达式：`(API_KEY|SECRET|PASSWORD|TOKEN)\s*=\s*["\'][\w-]+["\']`

**安全做法：**
```python
# 环境变量
API_KEY = os.environ.get("API_KEY")
if not API_KEY:
    raise ValueError("API_KEY not set")

# 密钥管理服务
import boto3
secrets = boto3.client('secretsmanager')
secret = secrets.get_secret_value(SecretId='my-secret')

# 配置文件（不提交到版本控制）
from dotenv import load_dotenv
load_dotenv()
API_KEY = os.getenv("API_KEY")
```

### 3. 加密与随机数（Cryptography & Random）

**不安全的随机数：**
```python
# ❌ 使用 random 模块生成密码/令牌
import random
token = ''.join(random.choices(string.ascii_letters, k=32))

# ❌ 使用 time 作为种子
random.seed(time.time())
```

**安全做法：**
```python
# ✅ 使用 secrets 模块
import secrets
token = secrets.token_urlsafe(32)
password = secrets.token_hex(16)

# ✅ 密码哈希
import bcrypt
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
```

### 4. 命令注入（Command Injection）

**危险模式：**
```python
# ❌ 用户输入直接传入系统命令
os.system(f"cat {user_input}")
subprocess.call(f"ls {user_dir}", shell=True)
```

**安全做法：**
```python
# ✅ 参数化执行
subprocess.run(["ls", user_dir], check=False)
# 或使用 shlex.quote
import shlex
subprocess.run(f"ls {shlex.quote(user_dir)}", shell=True)
```

## 检测工具集成

### 正则规则
```python
security_rules = [
    (r'execute\s*\(\s*[\"'][^\"']+%s', "SQL 注入风险"),
    (r'(API_KEY|SECRET|PASSWORD)\s*=\s*[\"''][\w-]+[\"'\'']', "硬编码密钥"),
    (r'random\.(choices|randint)\s*\(', "不安全的随机数"),
]
```

### AST 分析
- 检测 `os.system`、`subprocess.*` 调用与用户输入的组合
- 检测字符串拼接操作与敏感函数的组合
- 分析变量定义追踪敏感数据流

## 修复优先级
1. **Critical**：硬编码密钥、明显的 SQL 注入
2. **High**：命令注入、不安全的随机数用于安全场景
3. **Medium**：潜在的数据泄露路径
4. **Low**：加密算法选择不当

## 参考资料
- OWASP Top 10
- CWE-89: SQL Injection
- CWE-798: Use of Hard-coded Credentials
