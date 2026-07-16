# 敏感信息保护详解（Sensitive Information Protection）

## 概述
敏感信息保护涉及识别、分类和保护程序中的敏感数据，防止泄露和滥用。

## 敏感信息分类

### 1. 个人身份信息（PII）
- 身份证号、护照号
- 电话号码、邮箱地址
- 银行账号、信用卡号
- 生物识别信息（指纹、人脸）

### 2. 凭据信息
- 密码、PIN 码
- API 密钥、访问令牌
- 私钥、证书
- 数据库连接字符串

### 3. 业务敏感信息
- 商业机密、客户名单
- 财务数据、交易记录
- 源代码、算法细节
- 配置信息、部署架构

## 数据泄露路径

### 1. 日志泄露

**问题代码：**

```python
# ❌ 日志中记录密码
logger.info(f"User login: username={username}, password={password}")

# ❌ 日志中记录敏感请求
logger.debug(f"API request: {request_body}")  # 可能包含信用卡信息

# ❌ 异常栈暴露敏感数据
try:
    process_payment(card_number, cvv)
except Exception as e:
    logger.exception(f"Payment failed: {e}")  # 可能暴露卡号
```

**正确做法：**

```python
# ✅ 日志脱敏
logger.info(f"User login: username={username}, password={'*' * len(password)}")

# ✅ 过滤敏感字段
def log_request(request_body):
    safe_body = request_body.copy()
    for field in ['password', 'credit_card', 'cvv']:
        if field in safe_body:
            safe_body[field] = '***'
    logger.debug(f"API request: {safe_body}")

# ✅ 异常时不暴露敏感数据
try:
    process_payment(card_number, cvv)
except Exception as e:
    logger.error(f"Payment failed for user {user_id}")  # 只记录用户ID
    raise
```

### 2. 错误响应泄露

**问题代码：**

```python
# ❌ 错误信息暴露数据库结构
@app.errorhandler(Exception)
def handle_error(e):
    return {"error": str(e)}, 500  # 可能暴露 SQL 错误

# ❌ 错误信息暴露用户信息
def get_user_orders(user_id):
    try:
        return db.execute(f"SELECT * FROM orders WHERE user_id={user_id}")
    except Exception as e:
        return {"error": f"Failed for user {user_id}: {e}"}  # 暴露 user_id
```

**正确做法：**

```python
# ✅ 通用错误信息
@app.errorhandler(Exception)
def handle_error(e):
    logger.exception("Internal error")
    return {"error": "Internal server error"}, 500

# ✅ 安全的错误处理
def get_user_orders(user_id):
    try:
        return db.execute("SELECT * FROM orders WHERE user_id=?", (user_id,))
    except Exception as e:
        logger.error(f"Failed to fetch orders for user {user_id}")
        return {"error": "Failed to fetch orders"}
```

### 3. 配置文件泄露

**问题代码：**

```python
# ❌ 硬编码配置
DATABASE_URL = "postgresql://user:password@localhost/db"
API_KEY = "sk-1234567890abcdef"

# ❌ 配置文件提交到版本控制
# config.py
PRODUCTION_KEY = "production-secret-key"
```

**正确做法：**

```python
# ✅ 环境变量
DATABASE_URL = os.environ.get("DATABASE_URL")
API_KEY = os.environ.get("API_KEY")

if not API_KEY:
    raise ValueError("API_KEY environment variable not set")

# ✅ 配置文件分离
# config.example.py（提交到版本控制）
DATABASE_URL = "postgresql://user:password@localhost/db"
API_KEY = "your-api-key-here"

# config.py（不提交，使用 .gitignore）
DATABASE_URL = "postgresql://user:realpassword@localhost/db"
API_KEY = "real-api-key"
```

### 4. 数据传输泄露

**问题代码：**

```python
# ❌ 明文传输敏感数据
http.post("https://api.example.com/user", json={
    "username": username,
    "password": password  # 虽然 HTTPS 加密，但日志可能记录
})

# ❌ URL 参数传递敏感信息
requests.get(f"https://api.example.com/reset?email={user_email}&token={reset_token}")
# URL 参数会被记录在访问日志中
```

**正确做法：**

```python
# ✅ POST Body 传输敏感数据
http.post("https://api.example.com/user", json={
    "username": username,
    "password": password
}, headers={"Content-Type": "application/json"})

# ✅ 敏感信息放在 Body 而非 URL
requests.post("https://api.example.com/reset", json={
    "email": user_email,
    "token": reset_token
})
```

## 数据保护技术

### 1. 数据脱敏

```python
def mask_email(email: str) -> str:
    """邮箱脱敏"""
    if '@' not in email:
        return email
    local, domain = email.split('@', 1)
    if len(local) <= 2:
        return f"{local[0]}***@{domain}"
    return f"{local[:2]}***@{domain}"

def mask_phone(phone: str) -> str:
    """手机号脱敏"""
    if len(phone) != 11:
        return phone
    return f"{phone[:3]}****{phone[7:]}"

def mask_credit_card(card: str) -> str:
    """信用卡号脱敏"""
    if len(card) < 13:
        return card
    return f"****-****-****-{card[-4:]}"
```

### 2. 数据加密

```python
from cryptography.fernet import Fernet

# 加密敏感数据
def encrypt_data(data: str, key: bytes) -> bytes:
    f = Fernet(key)
    return f.encrypt(data.encode())

def decrypt_data(encrypted_data: bytes, key: bytes) -> str:
    f = Fernet(key)
    return f.decrypt(encrypted_data).decode()

# 使用示例
SECRET_KEY = Fernet.generate_key()

encrypted = encrypt_data("sensitive_data", SECRET_KEY)
decrypted = decrypt_data(encrypted, SECRET_KEY)
```

### 3. 安全存储

```python
import bcrypt
import hashlib

# 密码存储
def hash_password(password: str) -> str:
    """密码哈希存储"""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode(), salt)

def verify_password(password: str, hashed: str) -> bool:
    """验证密码"""
    return bcrypt.checkpw(password.encode(), hashed)

# 数据指纹
def create_data_fingerprint(data: str) -> str:
    """创建数据指纹用于比对，不存储原始数据"""
    return hashlib.sha256(data.encode()).hexdigest()
```

## 检测规则

### 正则模式

```python
sensitive_patterns = [
    (r'password\s*[:=]\s*[\"''][^\"'\'']+[\"'\'']', "密码可能明文存储"),
    (r'logger\.\w+\(.*password', "日志中包含密码"),
    (r'except.*:\s*print\s*\(\s*.*\buser\b', "错误处理可能暴露用户信息"),
    (r'API_KEY\s*[:=]\s*[\"'\''][\w-]+[\"'\'']', "API 密钥硬编码"),
]
```

### AST 分析

```python
# 检测日志函数中的敏感字段
if isinstance(node, ast.Call):
    if isinstance(node.func, ast.Attribute):
        if node.func.attr in ('info', 'debug', 'warning', 'error'):
            # 检查参数中是否包含敏感字段
            for arg in node.args:
                if contains_sensitive_field(arg):
                    report_issue("日志中可能包含敏感信息")
```

## 修复优先级

1. **Critical**：密码、密钥硬编码
2. **High**：日志中的敏感信息、错误响应泄露
3. **Medium**：配置文件中的敏感信息
4. **Low**：临时调试代码中的敏感信息

## 合规要求

### 1. GDPR（欧盟通用数据保护条例）
- 数据最小化原则
- 数据主体权利（访问、删除、移植）
- 数据保护设计（Privacy by Design）

### 2. PCI DSS（支付卡行业数据安全标准）
- 禁止存储完整信用卡号
- 传输中的数据加密
- 定期安全审计

### 3. 等保 2.0（中国网络安全等级保护）
- 数据分类分级
- 敏感数据加密存储
- 访问控制和审计

## 参考资料
- OWASP Top 10 - Sensitive Data Exposure
- GDPR 合规指南
- NIST Cybersecurity Framework
