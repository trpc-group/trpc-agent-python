# 资源泄漏规则

## 概述

检测代码中可能出现的资源泄漏模式，包括文件句柄、网络连接、数据库连接和内存泄漏。

## 规则列表

### RL-01: 文件句柄未关闭

**严重级别**: Warning

**描述**: 使用 `open()` 打开文件后未使用 `with` 语句或未显式调用 `close()`，可能导致文件句柄泄漏。

**检测模式**:
- `open(path).read()` — 未关闭文件句柄
- `f = open(path)` 后无对应的 `f.close()` 调用
- 异常路径中未释放文件句柄

**修复建议**:
```python
# 错误
f = open("data.txt", "r")
content = f.read()

# 正确
with open("data.txt", "r") as f:
    content = f.read()
```

### RL-02: 连接未释放

**严重级别**: Warning

**描述**: 数据库连接、HTTP 连接等网络资源在使用后未释放。

**检测模式**:
- `sqlite3.connect()` 后无 `connection.close()`
- `pymongo.MongoClient()` 后无 `client.close()`
- `redis.Redis()` 后无连接释放

### RL-03: 内存泄漏模式

**严重级别**: Suggestion

**描述**: 在循环中累积数据、未清理的缓存、全局变量增长等内存泄漏模式。

**检测模式**:
- 在循环中向列表追加大量数据
- 未设置上限的 LRU 缓存
- 模块级别的可变数据结构持续增长

**修复建议**:
```python
# 处理大文件时使用流式读取
def process_large_file(path):
    with open(path, "r") as f:
        for line in f:
            yield process(line)

# 使用生成器处理大数据集
def get_large_data():
    for item in db.query_large():
        yield transform(item)
```

### RL-04: 资源未在异常路径中释放

**严重级别**: Warning

**描述**: 在异常发生时，已分配的资源未正确释放。

**检测模式**:
- 在 `try` 块中分配资源，但 `except` 或 `finally` 中未释放
- 提前 `return` 时未释放资源