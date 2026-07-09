# Code Review Report — 70c2a283fdb8468cac5a31908a348a2c

## 1. Findings 摘要（4 条）
- [high] app/secret.py:1 SEC003: 硬编码密钥 — 密钥类变量直接赋字面量,应从环境变量/密钥管理读取 (conf=0.8)
- [high] app/secret.py:2 SEC003: 硬编码密钥 — 密钥类变量直接赋字面量,应从环境变量/密钥管理读取 (conf=0.8)
- [critical] app/secret.py:1 SEN002: OpenAI API key 明文 (conf=0.95)
- [critical] app/secret.py:2 SEN003: GitHub personal access token 明文 (conf=0.95)

## 2. 严重级别统计
- critical: 2
- high: 2
- medium: 0
- low: 0

## 3. 人工复核项（0 条）

## 4. Filter 拦截摘要（0 条）

## 5. 监控指标
- total_duration_ms: 5994
- sandbox_duration_ms: 14
- tool_calls: 0
- blocks: 0
- finding_count: 4
- exception_types: {}

## 6. 沙箱执行摘要（1 次）
- fake: status=ok dur=0ms timed_out=0 masked=0

## 7. 可执行修复建议
- app/secret.py:1: 使用参数化查询/参数列表替代字符串拼接；密钥从环境变量或密钥管理服务读取
- app/secret.py:2: 使用参数化查询/参数列表替代字符串拼接；密钥从环境变量或密钥管理服务读取
- app/secret.py:1: 立即轮换该凭证并从代码中移除，改用密钥管理服务注入
- app/secret.py:2: 立即轮换该凭证并从代码中移除，改用密钥管理服务注入

## 8. Warnings（0 条，低置信度，不混入 findings）
