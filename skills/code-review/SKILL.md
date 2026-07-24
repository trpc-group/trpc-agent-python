---
name: code-review
description: 自动化代码评审 Agent，支持变更分析、静态检查、规则扫描和完整报告生成
---

# Code Review Skill

自动化代码评审 Skill，支持 8 步完整评审工作流。

## 8 步代码评审工作流

### 1. 变更摘要分析
使用 `skill_run` 执行 `scripts/diff_summary.py`，从标准输入读取 diff 并输出变更摘要：
- 变更文件列表（新增/修改/删除）
- 变更行数统计
- 主要变更模块识别

```python
skill_run(skill="code-review", command="python scripts/diff_summary.py", stdin=diff_content)
```

### 2. 静态代码检查
使用 `skill_run` 执行 `scripts/static_review.py`，对变更文件执行静态分析：
- 基于 Python AST 的语法检查
- 潜在 bug 识别（未处理的异常、资源泄漏等）
- 代码风格一致性检查

```python
skill_run(skill="code-review", command="python scripts/static_review.py", output_files=["out/static_review.json"])
```

### 3. 安全规则检查
对照 `rules/security.md` 中的安全规则，检查代码是否存在：
- SQL 注入风险
- 硬编码密钥/密码
- 不安全的随机数生成
- 未验证的用户输入

### 4. 异常处理审查
对照 `rules/async_errors.md`，检查异步代码和错误处理：
- async/await 正确使用
- 异常捕获范围合理性
- 异常信息不泄露敏感数据

### 5. 资源泄漏检查
对照 `rules/resource_leak.md`，检查资源管理：
- 文件句柄未关闭
- 数据库连接未释放
- 临时文件未清理

### 6. 数据库生命周期审查
对照 `rules/db_lifecycle.md`，检查数据库操作：
- 事务边界清晰
- 连接池使用正确
- 避免 N+1 查询

### 7. 敏感信息检查
对照 `rules/sensitive_information.md`，检查：
- 用户隐私数据保护
- 日志中的敏感信息
- API 密钥/Token 处理

### 8. 测试覆盖度分析
对照 `rules/missing_tests.md`，评估：
- 新增功能的单元测试覆盖
- 边界条件测试
- 异常场景测试

## 使用方式

### 完整评审流程
1. 调用 `skill_load("code-review")` 加载 Skill
2. 准备 diff 内容（git diff 输出）
3. 执行 `skill_run` 运行 `diff_summary.py` 和 `static_review.py`
4. 根据规则文档逐项审查
5. 生成完整评审报告

### 输出格式
评审报告包含以下部分：
- **变更概览**：文件列表、统计摘要
- **静态分析结果**：潜在问题列表
- **规则检查结果**：8 类规则的合规性评估
- **改进建议**：优先级排序的修复建议

## 规则参考

详细规则说明参见 `references/` 目录：
- `security.md` - 安全规则详解
- `async_errors.md` - 异步编程最佳实践
- `resource_leak.md` - 资源管理模式
- `db_lifecycle.md` - 数据库操作规范
- `sensitive_information.md` - 数据保护指南
- `missing_tests.md` - 测试策略建议

## 工具集成

本 Skill 集成了以下工具：
- **自定义脚本**：`scripts/diff_summary.py`、`scripts/static_review.py`
- **规则引擎**：基于正则和 AST 的静态检查
- **报告生成**：结构化 JSON + Markdown 报告

适用于 Pull Request 自动化评审、代码质量门禁、持续集成流程。
