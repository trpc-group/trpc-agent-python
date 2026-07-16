# 异步与异常处理（Async Errors）

## 检查项

### 1. async/await 使用
- ❌ 忘记 await async 函数
- ❌ 在同步上下文调用 async 函数
- ✅ 正确使用 async/await

### 2. 异常捕获范围
- ❌ 过于宽泛的 `except:` 或 `except Exception:`
- ❌ 吞掉异常不记录日志
- ✅ 精确捕获特定异常并记录

### 3. 异常信息泄露
- ❌ 异常中直接返回用户敏感数据
- ❌ 将数据库错误详情暴露给前端
- ✅ 异常信息脱敏后再输出

### 4. 资源清理
- ❌ 异常发生时资源未释放
- ✅ 使用 `try-finally` 或 `async with`

## 示例代码

### ❌ 错误示例
```python
# 忘记 await
result = async_fetch()  # 返回 coroutine 而非结果

# 过于宽泛的异常捕获
try:
    risky_operation()
except:
    pass  # 吞掉所有异常

# 异常信息泄露
except Exception as e:
    return {"error": str(e)}  # 可能泄露数据库结构
```

### ✅ 正确示例
```python
# 正确的 async/await
result = await async_fetch()

# 精确捕获异常
try:
    risky_operation()
except ValueError as e:
    logger.error(f"Invalid value: {e}")
    raise

# 异常脱敏
except Exception as e:
    logger.error(f"Operation failed: {e}")
    return {"error": "Internal server error"}
```

## 检测方法
- AST 分析：检查 `async def` 函数内的非 await 调用
- 正则匹配：`except:\s*$`、`except\s+Exception:`
