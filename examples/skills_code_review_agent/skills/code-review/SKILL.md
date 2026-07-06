---
name: code-review
description: Automated code-review rules and scripts — parse a unified diff, run security/async/resource/secret/DB-lifecycle/missing-tests checks, and emit structured findings JSON.
---

# code-review Skill

自动代码评审技能：解析 unified diff，运行六类静态审查规则，输出结构化 findings。
Automated code-review skill: parse a unified diff, run six categories of static
review rules, and emit structured findings.

## Contents 目录结构

- `SKILL.md` — this document 本说明
- `rules/` — human-readable rule documentation, one file per category
  规则文档（每类一份）: `security-risk.md`, `async-errors.md`, `resource-leaks.md`,
  `missing-tests.md`, `secret-leakage.md`, `db-lifecycle.md`
- `scripts/parse_diff.py` — raw diff → normalized changeset JSON
- `scripts/run_checks.py` — changeset JSON → findings JSON
- `scripts/lib/` — stdlib-only rule library (diff parser, rule engine, rule
  modules, canonical secret-pattern table)

## Commands 命令

Inside the staged workspace (`$WORKSPACE_DIR`):

```bash
# 1. Parse a raw diff placed at work/inputs/raw.diff
python3 skills/code-review/scripts/parse_diff.py work/inputs/raw.diff work/diff.json

# 2. Run all review rules; findings land in out/findings.json
python3 skills/code-review/scripts/run_checks.py work/diff.json out/findings.json \
    --files-dir work/inputs/files
```

## Inputs 输入

- `work/inputs/raw.diff` — unified diff text (git diff / PR patch)
- `work/inputs/diff.json` — alternatively, a pre-parsed changeset
- `work/inputs/files/` — optional full new-file contents (`{path}` layout) for
  higher-accuracy whole-file heuristics

## Outputs 输出

`out/findings.json`:

```json
{"findings": [{"severity": "high", "category": "security_risk", "file": "app.py",
               "line": 12, "title": "...", "evidence": "...", "recommendation": "...",
               "confidence": 0.9, "source": "static_rule", "rule_id": "SEC001"}]}
```

- `severity`: critical | high | medium | low | info
- `category`: security_risk | async_error | resource_leak | missing_tests |
  secret_leakage | db_lifecycle
- `confidence`: 0.0–1.0; the host agent routes < min_confidence findings to the
  needs_human_review bucket (noise control 降噪).
- Secret evidence is ALWAYS pre-redacted inside the sandbox — plaintext
  credentials never leave `scripts/lib/rule_secrets.py`.

## Environment 环境

Scripts are stdlib-only (Python ≥ 3.9) and run with a whitelisted environment;
they need no network, no site-packages and never write outside the workspace.
`--force-fail` on `run_checks.py` raises deterministically — the host uses it
to verify that sandbox failures cannot crash a review task.
