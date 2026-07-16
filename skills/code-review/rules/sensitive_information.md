# 敏感信息保护（Sensitive Information）

## 检查项

### 1. 用户隐私数据
- ❌ 日志中记录密码/身份证号
- ❌ 错误信息暴露用户邮箱
- ✅ 敏感字段脱敏处理

### 2. 日志安全
- ❌ 记录完整的请求参数
- ❌ 日志中包含 API 密钥
- ✅ 过滤敏感字段后再记录

### 3. 数据传输
- ❌ 明文传输敏感数据
- ❌ HTTP 传输密码
- ✅ 使用 HTTPS/TLS 加密

### 4. 数据存储
- ❌ 明文存储密码
- ❌ 数据库中直接存储信用卡号
- ✅ 使用强哈希和加密算法

## 示例代码

### ❌ 错误示例
```python
# 日志中记录密码
logger.info(f"User login: {username}, password: {password}")

# 错误信息暴露敏感数据
except Exception as e:
    return {"error": f"Failed for email {user.email}"}

# 明文存储密码
password = request.form["password"]
db.execute(f"INSERT INTO users (password) VALUES ('{password}')")
```

### ✅ 正确示例
```python
# 日志脱敏
logger.info(f"User login: {username}, password: {'*' * len(password)}")

# 错误信息不暴露敏感数据
except Exception as e:
    logger.error(f"Login failed for user {user_id}")
    return {"error": "Invalid credentials"}

# 密码哈希存储
password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
db.execute("INSERT INTO users (password_hash) VALUES (?)", (password_hash,))
```

## 检测方法
- 正则匹配：`password.*=.*["\']`、`logger\.\w+.*password`
- AST 分析：检查日志函数调用中的敏感字段
- 数据流分析：追踪敏感数据的流向
