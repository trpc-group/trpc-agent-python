# Finding Schema

The code-review Agent should emit structured findings so downstream filters, storage, reports, and future PR-comment integrations can consume the same data contract.

## Minimal finding fields

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `severity` | string | yes | `info`, `low`, `medium`, `high`, or `critical`. |
| `category` | string | yes | Review category, for example `security`, `async`, `resource_leak`, `test_coverage`, `secrets`, or `database_lifecycle`. |
| `file` | string | yes | Repository-relative file path. |
| `line` | integer | yes | New-file line number from the diff. |
| `title` | string | yes | One-line summary. |
| `evidence` | string | yes | Concrete diff, code, test, or sandbox evidence. |
| `recommendation` | string | yes | Actionable fix guidance. |
| `confidence` | string | yes | `low`, `medium`, or `high`. |
| `source` | string | yes | `skill`, `sandbox`, `filter`, or `fake_model`. |

## Recommended future fields

| Field | Description |
| --- | --- |
| `fingerprint` | Stable dedupe key derived from file, line, category, and normalized evidence. |
| `line_start` / `line_end` | Line span for multi-line findings. |
| `needs_human_review` | Whether the finding should be manually checked before promotion. |
| `raw_source` | Optional debugging trace, never shown directly in public output. |

## Example

```json
{
  "severity": "high",
  "category": "secrets",
  "file": "src/config.py",
  "line": 42,
  "title": "Hard-coded API token in configuration",
  "evidence": "The added line assigns a token-like literal to API_TOKEN.",
  "recommendation": "Move the token to a secret manager or environment variable and rotate the exposed value.",
  "confidence": "high",
  "source": "skill"
}
```

## Validation rules

- Findings should anchor to changed lines in the new file.
- Findings without concrete evidence should be downgraded or routed to `needs_human_review`.
- Duplicate findings for the same file, line, and category should be merged.
- Reports and database rows must not contain unredacted secrets.

---

# 中文说明

# Finding 结构

代码评审 Agent 应输出结构化 findings，这样后续的 Filter、存储、报告和未来 PR 评论集成都可以使用同一套数据契约。

## 最小 finding 字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `severity` | string | 是 | `info`、`low`、`medium`、`high` 或 `critical`，表示严重级别。 |
| `category` | string | 是 | 问题类别，例如 `security`、`async`、`resource_leak`、`test_coverage`、`secrets` 或 `database_lifecycle`。 |
| `file` | string | 是 | 仓库相对路径。 |
| `line` | integer | 是 | diff 中新文件行号。 |
| `title` | string | 是 | 一句话摘要。 |
| `evidence` | string | 是 | 具体 diff、代码、测试或沙箱证据。 |
| `recommendation` | string | 是 | 可执行修复建议。 |
| `confidence` | string | 是 | `low`、`medium` 或 `high`，表示置信度。 |
| `source` | string | 是 | `skill`、`sandbox`、`filter` 或 `fake_model`，表示 finding 来源。 |

## 推荐扩展字段

| 字段 | 说明 |
| --- | --- |
| `fingerprint` | 稳定去重键，由文件、行号、类别和标准化证据生成。 |
| `line_start` / `line_end` | 多行 finding 的行号范围。 |
| `needs_human_review` | 是否需要人工复核后才能提升为正式 finding。 |
| `raw_source` | 可选调试来源信息，不直接展示在公开输出中。 |

## 示例

```json
{
  "severity": "high",
  "category": "secrets",
  "file": "src/config.py",
  "line": 42,
  "title": "Hard-coded API token in configuration",
  "evidence": "The added line assigns a token-like literal to API_TOKEN.",
  "recommendation": "Move the token to a secret manager or environment variable and rotate the exposed value.",
  "confidence": "high",
  "source": "skill"
}
```

中文解释：

```text
在 src/config.py 第 42 行发现高危 secrets 问题：新增代码疑似把 API token 写死在配置里。
建议把 token 移到 secret manager 或环境变量中，并轮换已经暴露的值。
```

## 校验规则

- findings 应锚定到新文件的变更行。
- 没有具体证据的 findings 应降级或进入 `needs_human_review`。
- 同一文件、同一行、同一类别的重复 findings 应合并。
- 报告和数据库记录中不能包含未脱敏 secrets。
