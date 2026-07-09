# Phase 2 — 规则引擎 交付概览

> 自动代码评审 Agent · Phase 2 (Rules engine)
> 基于 tRPC-Agent Skill 体系 · 完成日期 2026-07-07

## 完成内容

按 `docs/skills_code_review_agent/specs/phase-2-rules-engine.md` 契约，实现了 6 类规则文档（每类≥3 条）、`run_checks.py` 规则匹配引擎（产出 `RawFinding`）和 `mask_secrets.py` 敏感信息脱敏器。本阶段产出的是**未去重、未分流**的原始诊断，交 P4 处理。

### 交付物

| 文件 | 职责 |
|------|------|
| `skills/code-review/rules/*.md` | 6 类规则文档，每类含机器可读 ```yaml 规则块（共 22 条规则） |
| `skills/code-review/scripts/run_checks.py` | `RawFinding` + `load_rules` + `run_checks` + 6 个 checker + AST 增强 + CLI |
| `skills/code-review/scripts/mask_secrets.py` | `mask_secrets(text)->(masked, count)` + 正则集 + Shannon 熵值 + CLI |
| `tests/test_phase2_rules_engine.py` | Phase 2 验收测试（40 用例） |

### 规则统计

| 类别 | 规则数 | 检测方式 |
|------|--------|----------|
| security | 4 (SEC001-004) | pattern：SQL注入/命令注入/硬编码密钥/不安全反序列化 |
| sensitive | 6 (SEN001-006) + 熵值 | pattern：AKIA/sk-/ghp_/password/私钥/连接串 + Shannon熵>4.5 |
| async | 3 (ASY001-003) | ast：gather未await/ClientSession未async with/async函数裸调用 |
| resource | 3 (RES001-003) | ast：open未with/connect未with/try无finally |
| db | 3 (DB001-003) | ast：connect未with/cursor未close/begin无rollback |
| tests | 3 (TST001-003) | diff：新增公开函数/类/路由无对应测试 |

**合计 22 条规则，每类≥3 条，满足 DoD。**

## 关键设计决策

1. **规则文档双格式**：人类可读 Markdown 说明 + ```yaml 机器可读规则块（id/pattern/severity_hint/confidence/type/description）。`run_checks.load_rules` 解析 yaml 块，pattern 预编译为正则。规则文档可读性与机器可读性兼得。

2. **内置 YAML 子集解析器（零依赖）**：环境无 pyyaml，`run_checks._parse_rules_yaml` 专门处理规则文档的 list-of-dict 结构，正确处理单引号 `''` 转义（让含 `["']` 的正则 pattern 能正确解析）、float 推断。优先 `import yaml`，失败回退内置。

3. **只分析 add 行 + confidence 分层控误报**：del 行不报新问题。精确/已知格式命中 `confidence` 0.9+（→P4 findings），启发式 AST/pattern 0.6-0.75（→P4 warnings）。规格意图：低置信让 P4 分流，避免污染高置信结论。

4. **pattern 为主 + AST best-effort 增强**：所有规则都有 pattern 正则（可靠、可测、高性能 re.compile）。async/resource/db 额外做 best-effort AST 分析（`textwrap.dedent` + `ast.parse`，失败静默跳过），确认命中提升 confidence 到 0.8+。diff add 行常不完整，AST 为增强而非依赖。

5. **降误报过滤**：resource/db 的 `open()/connect()` 若 add 行含 `with` 前缀则跳过；async 的 `gather/create_task` 若含 `await` 则跳过。sensitive 的熵值检测对已知格式命中的行去重（token 是已命中 evidence 的子串则不重复报 SEN_ENT）。

6. **missing_tests 跨文件关联**：从所有非测试文件的 add 行提取新增 `def`/`class`，在测试文件（`test_*.py`/`tests?/`）的 add 行里找 `test_<fn>`/`<fn>(` 引用，未命中 → finding。私有（`_` 前缀）不报。

7. **mask_secrets 与 sensitive 规则共享正则**：脱敏器的正则集（AKIA/sk-/ghp_/password/私钥/连接串）与 SEN001-006 一致，确保"检出什么就脱敏什么"。熵值检测共享 `_TOKEN_RE` + `_shannon_entropy`。

## 验收结果（DoD 全部达成）

| # | 验收标准 | 状态 | 证据 |
|---|----------|------|------|
| 1 | 6 类规则文档齐全，每类≥3 条 | ✅ | `TestRuleDocs` 4 用例（22 条规则，字段完整） |
| 2 | run_checks 每类样本检出 | ✅ | security/sensitive/async/resource/db/tests 各类检出用例（含 `with`/`await` 过滤） |
| 3 | RawFinding 字段齐全，confidence 合理 | ✅ | `TestRawFindingFields` 3 用例（精确≥0.9，启发≤0.75） |
| 4 | mask_secrets 脱敏 + count 正确 | ✅ | `TestMaskSecrets` 10 用例（6 格式 + 多密钥计数 + 熵值 + 空文本） |
| 5 | 无问题 diff 产空列表 | ✅ | `TestCleanDiff` 3 用例（clean fixture / trivial diff / 空 changeset） |

**测试统计**：Phase 2 共 40 用例通过（0.13s）；Phase 0/1 回归全过。累计 **90 用例 0 失败**。

## 运行方式

```bash
# 规则引擎 CLI（diff 文件 → findings JSON）
python examples/skills_code_review_agent/skills/code-review/scripts/run_checks.py \
  --skill-dir examples/skills_code_review_agent/skills/code-review \
  --diff-file my.diff

# 脱敏器 CLI（stdin → 脱敏文本 + count）
python examples/skills_code_review_agent/skills/code-review/scripts/mask_secrets.py < input.txt

# 验收测试
python examples/skills_code_review_agent/tests/test_phase2_rules_engine.py
```

## 下游影响

- **P3（沙箱 + Filter）**：`run_checks.py` 与 `mask_secrets.py` 是沙箱内可执行脚本，`skill_load` 的 `scripts` 清单已自动包含它们；Filter 门禁将审查这些脚本。沙箱输出落地前用 `mask_secrets` 脱敏。
- **P4（去重结构化）**：消费 `RawFinding` 列表，按 `(file, line, category)` 去重取最高 confidence，按置信度阈值分流到 findings/warnings/needs_human_review。本阶段的 confidence 分层直接服务 P4 分流。
- `agent.py` 的 `skill_load` 已加 `skill_dir` 字段，P3/P4 可直接定位规则文档与脚本。
