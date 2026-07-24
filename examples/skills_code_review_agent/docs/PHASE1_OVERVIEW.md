# Phase 1 — 输入与 Skill 加载 交付概览

> 自动代码评审 Agent · Phase 1 (Input & Skill loading)
> 基于 tRPC-Agent Skill 体系 · 完成日期 2026-07-07

## 完成内容

按 `docs/skills_code_review_agent/specs/phase-1-input-skill.md` 契约，实现了 unified diff 解析器与 code-review Skill 加载器，产出结构化 `ChangeSet` 与 `RuleSet`，并将解析结果落库到 Phase 0 的 `input_diff` 表。

### 交付物

| 文件 | 职责 |
|------|------|
| `skills/code-review/SKILL.md` | Skill 契约 frontmatter（name/entry/rules/sandbox） |
| `skills/code-review/scripts/parse_diff.py` | unified diff → `ChangeSet`（数据结构 + 解析器 + CLI） |
| `skills/code-review/rules/*.md` | 6 类规则骨架（security/async_errors/resource_leak/missing_tests/sensitive_info/db_lifecycle） |
| `agent.py` | `skill_load()` + 输入采集 + 落库编排 + CLI |
| `tests/test_phase1_input_skill.py` | Phase 1 验收测试（30 用例） |

### 数据结构（`parse_diff.py`）

```
ChangeSet
 └─ files: list[ChangedFile]
     ├─ path, status(added|modified|deleted), hunks
     ├─ added_lines / deleted_lines / line_count / hunk_count (property)
     └─ hunks: list[Hunk]
         ├─ old_start/new_start/old_count/new_count
         └─ lines: list[DiffLine]
             └─ type(add|del|ctx), content, new_line_no(add/ctx 有, del=None)
```

**行号规则**：`new_line_no` 从 hunk 头 `@@ -a,b +c,d @@` 的 `c` 开始，仅在 add/ctx 行递增，del 行为 `None`。

## 关键设计决策

1. **`skill_load` 扫描文件系统而非读 frontmatter 声明**：rules/scripts 清单来自 `rules/*.md` 和 `scripts/*.py` 的实际磁盘扫描（排序相对路径），frontmatter 只提供 `name` 和 `sandbox_config`。这样 P2/P3/P4 新增脚本时自动出现在清单中，无需同步改 SKILL.md。

2. **零依赖 YAML frontmatter 解析**：环境无 pyyaml。`skill_load` 优先 `import yaml`，失败回退到内置缩进感知解析器（处理标量/块 list/嵌套 dict/`>-` 多行/行内 `[a,b]`/int 推断）。验证了 sandbox_config 的 `timeout_s=30`、`max_output_bytes=1048576` 为 int，`env_whitelist` 为 list。

3. **解析器单遍行迭代 + 状态机**：按 `diff --git` / `--- ` / `+++ ` / `@@ ` 推进状态，避免全量字符串复制（大 diff 性能）。容错跳过 binary/rename/mode-change/meta 行，空 diff 返回空 `ChangeSet`，`@@ -1 +1 @@` 无逗号计数默认 1。

4. **`ChangedFile` 内置统计 property**：`added_lines`/`deleted_lines`/`line_count`/`hunk_count` 直接服务落库，`agent.persist_changeset` 一次性写入 `input_diff`（file_path/sha256/hunk_count/line_count/summary），sha256 基于文件变更内容规范化文本计算，用于去重标识。

5. **三种输入模式统一 `load_diff()`**：
   - `--diff-file`：读文件
   - `--repo-path`：`git diff HEAD`（含已暂存，subprocess 容错）
   - `--fixture`：内置 `clean`/`security` 样本（dry-run 无需真实 diff）

## 验收结果（DoD 全部达成）

| # | 验收标准 | 状态 | 证据 |
|---|----------|------|------|
| 1 | `parse_diff` 解析标准 unified diff（文件/hunk/行号） | ✅ | `TestParseDiff` 9 用例 |
| 2 | add 行 `new_line_no` 正确 | ✅ | `TestParseDiffLineNumbers` 4 用例（含多 hunk 独立、del=None） |
| 3 | `skill_load` 读 frontmatter 产出 rules+scripts+sandbox_config | ✅ | `TestSkillLoad` 5 用例（含类型断言） |
| 4 | ChangeSet 落库 input_diff 表 | ✅ | `TestChangeSetPersist` 3 用例 + `get_task` join 验证 |
| 5 | 支持 --diff-file / --repo-path / fixture | ✅ | `TestInputModes` 6 用例（含真实 git repo 集成） + `TestCLIIntegration` 2 端到端 |

**测试统计**：Phase 1 共 30 用例全部通过（1.7s）；Phase 0 回归 20 用例全过。累计 50 用例 0 失败。

## 运行方式

```bash
# fixture 模式（dry-run 演示）
python examples/skills_code_review_agent/agent.py --fixture security --db-path cr.db

# diff 文件模式
python examples/skills_code_review_agent/agent.py --diff-file my.diff --db-path cr.db

# repo 模式（git diff HEAD）
python examples/skills_code_review_agent/agent.py --repo-path /path/to/repo --db-path cr.db

# 解析器 CLI（stdin/file → JSON）
python examples/skills_code_review_agent/skills/code-review/scripts/parse_diff.py < my.diff

# 验收测试
python examples/skills_code_review_agent/tests/test_phase1_input_skill.py
```

## 下游影响

- **P2（规则引擎）**：消费 `ChangeSet`，按 6 类 `rules/*.md` 跑静态检查，规则骨架已就位待填充检测逻辑。
- **P3（沙箱 + Filter）**：消费 `skill_load` 的 `scripts` 清单送入 Filter 门禁，`sandbox_config` 驱动沙箱策略。
- 落库的 `input_diff` 行已带 sha256/hunk_count/line_count/summary，供 P4 去重与 P6 报告引用。
