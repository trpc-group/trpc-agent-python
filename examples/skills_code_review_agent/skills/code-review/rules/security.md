# 安全风险规则

## 概述

检测代码变更中引入的安全风险，包括 SQL 注入、命令注入、路径遍历、XSS 和动态代码执行等。

## 规则列表

### SR-01: SQL 注入

**严重级别**: Critical

**描述**: 使用 f-string 或字符串拼接构造 SQL 查询，可能导致 SQL 注入攻击。

**检测模式**:
- `cursor.execute(f"..." + ...)` — 拼接 SQL 查询
- `cursor.execute(f'...' % ...)` — 格式化 SQL 查询
- 使用 ORM raw SQL 时未使用参数化查询

**修复建议**:
```python
# 错误
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")

# 正确
cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
```

### SR-02: 命令注入

**严重级别**: Critical

**描述**: 使用 `os.system()`、`subprocess.call(shell=True)` 等可能被注入恶意命令。

**检测模式**:
- `os.system(f"...{user_input}...")` — 拼接系统命令
- `subprocess.call(cmd, shell=True)` — 启用 shell 解析
- `subprocess.Popen(cmd, shell=True)` — 启用 shell 解析

**修复建议**:
```python
# 错误
os.system(f"ls -l {user_input}")

# 正确
subprocess.run(["ls", "-l", safe_path])
```

### SR-03: 路径遍历

**严重级别**: Warning

**描述**: 直接使用用户输入构造文件路径，可能导致路径遍历攻击。

**检测模式**:
- `open(user_input, ...)` — 直接使用用户输入作为路径
- 未使用 `os.path.abspath()` 或 `os.path.realpath()` 规范化路径
- 未检查路径是否在允许的基目录内

**修复建议**:
```python
# 错误
with open(user_input, "r") as f:

# 正确
safe_path = os.path.realpath(os.path.join(BASE_DIR, user_input))
if not safe_path.startswith(BASE_DIR):
    raise ValueError("Invalid path")
with open(safe_path, "r") as f:
```

### SR-04: 动态代码执行

**严重级别**: Critical

**描述**: 使用 `eval()`、`exec()` 或 `__import__()` 执行动态代码，可能导致任意代码执行。

**检测模式**:
- `eval(user_input)` — 动态求值
- `exec(user_code)` — 动态执行
- `__import__(module_name)` — 动态导入

### SR-05: 敏感信息泄露

**严重级别**: Critical

**描述**: 在日志、错误信息或响应中输出敏感信息。

**检测模式**:
- 在异常处理中直接输出原始异常信息
- 在日志中记录密码、Token 等敏感字段
- Debug 模式下未关闭敏感信息输出