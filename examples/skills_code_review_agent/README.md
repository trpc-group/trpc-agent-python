# Code Review Agent

基于 tRPC-Agent Skills + 沙箱 + 数据库的自动化代码评审 Agent。

## 功能概述

读取 git diff → 安全检查（Filter）→ 规则扫描（6 类）→ 沙箱执行 → 去重脱敏 → 结构化报告 → 数据库存储。

## 快速开始

```bash
# 安装依赖
pip install -e ".[gepa]"

# 运行单个 diff 评审
python run_review.py --diff-file fixtures/diffs/security.diff

# Dry-run 模式（无需 API Key）
python run_review.py --diff-file fixtures/diffs/security.diff --dry-run

# 运行测试
python -m pytest tests/ -v
```

## 扫描规则（6 类）

| 类别 | 说明 | 示例 |
|------|------|------|
| Security | 命令注入、不安全反序列化、动态导入 | `os.system()`, `eval()`, `pickle.loads()` |
| Async Error | 事件循环阻塞、缺少 await | `time.sleep()` in async, 未 await 协程 |
| Resource Leak | 文件句柄未关闭、连接泄漏 | `open()` 无 with, HTTP 无 session |
| DB Lifecycle | 游标/连接未关闭、事务未提交 | `cursor.execute()` 无 commit |
| Missing Tests | 新函数无对应测试 | `def new_func()` 无 `test_new_func` |
| Secret Info | 硬编码密钥/密码/Token | API keys, GitHub tokens, JWT |

## 目录结构

```
examples/skills_code_review_agent/
├── run_review.py          # 入口
├── pipeline/              # 评审 pipeline
│   ├── diff_parser.py     # Unified diff 解析
│   ├── scanners.py        # 6 类规则扫描器
│   ├── filter_chain.py    # Filter 安全拦截
│   ├── sandbox.py         # 沙箱执行
│   ├── dedup.py           # 去重降噪
│   ├── redaction.py       # 敏感信息脱敏
│   ├── report.py          # JSON/MD 报告生成
│   └── telemetry.py       # 监控审计
├── storage/               # SQLite 持久化
│   ├── schema.sql         # 数据库 Schema
│   ├── models.py          # 数据模型
│   └── dao.py             # 数据访问层
├── fixtures/diffs/        # 8 个测试 diff
├── tests/                 # 测试（116 个）
└── skills/code-review/    # Code-review Skill 定义
```

## 输出格式

### JSON (`review_report.json`)
```json
{
  "task_id": "review-20260707-...",
  "summary": { "total_findings": 6, "by_severity": {...} },
  "findings": [
    {
      "severity": "critical",
      "category": "security",
      "file": "handler.py",
      "line": 8,
      "title": "os.system() with unsanitized input",
      "evidence": "os.system(user_input)",
      "recommendation": "Use subprocess.run() with shell=False",
      "confidence": 0.9,
      "source": "security_scanner"
    }
  ],
  "filter_summary": {...},
  "sandbox_summary": {...},
  "telemetry": {...}
}
```

### Markdown (`review_report.md`)
- Findings 摘要和严重级别统计
- 人工复核项
- Filter 拦截摘要
- 沙箱执行摘要
- 可执行修复建议

## 数据库

SQLite 存储，4 张表：
- `review_tasks` — 评审任务
- `findings` — 发现的问题
- `sandbox_runs` — 沙箱执行记录
- `filter_logs` — Filter 拦截日志
