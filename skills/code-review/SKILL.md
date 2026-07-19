---
name: code-review
description: Reviews diffs with reusable rules and sandboxed scripts. Invoke when a task needs structured code review, risk detection, or validation runs.
---

# Code Review Skill

Overview

Use this skill when a task needs structured code review over a unified diff,
local repository changes, or deterministic fixtures. It packages reusable review
rules, sandboxable scripts, and output conventions for the
`skills_code_review_agent` example.

This skill is designed for:

- deterministic first-pass code review
- security and secret scanning on changed lines
- review validation in dry-run or fake-model mode
- controlled script execution through `skill_run`

Primary workflow

1. Load the skill:

   ```text
   skill_load(skill="code-review")
   ```

2. If more detail is needed, load docs such as `USAGE.md` or `SCRIPT_CONTRACTS.md`.

3. Run one or more skill scripts through `skill_run`:

   - `scripts/parse_diff.py`
   - `scripts/run_linters.py`
   - `scripts/run_tests.py`

4. Convert results into structured review findings with the required fields:

   - `severity`
   - `category`
   - `file`
   - `line`
   - `title`
   - `evidence`
   - `recommendation`
   - `confidence`
   - `source`

Inputs

- unified diff files
- repository paths with local modifications
- prepared fixtures for deterministic tests

Outputs

- structured findings
- sandbox execution summaries
- `review_report.json`
- `review_report.md`

Available Docs

- `RULES.md`: review categories and rule semantics
- `USAGE.md`: when and how to invoke the skill
- `SCRIPT_CONTRACTS.md`: each script's CLI and output contract

Scripts

- `scripts/parse_diff.py`
- `scripts/run_linters.py`
- `scripts/run_tests.py`

Safety Guidance

- Always pass review inputs as files rather than inlining huge diffs in prompts.
- Keep execution deterministic in dry-run and fake-model mode.
- Do not allow high-risk scripts to bypass filter checks.
- Redact secrets before persisting or displaying evidence.

Example `skill_run` commands

1. Parse a diff:

   ```text
   skill_run(
     skill="code-review",
     cwd="$SKILLS_DIR/code-review",
     command="python scripts/parse_diff.py --diff-file $WORK_DIR/sample.diff > out/parse_summary.json",
     output_files=["out/parse_summary.json"]
   )
   ```

2. Run deterministic lint checks:

   ```text
   skill_run(
     skill="code-review",
     cwd="$SKILLS_DIR/code-review",
     command="python scripts/run_linters.py --diff-file $WORK_DIR/sample.diff > out/lint_summary.json",
     output_files=["out/lint_summary.json"]
   )
   ```

3. Run deterministic test-presence checks:

   ```text
   skill_run(
     skill="code-review",
     cwd="$SKILLS_DIR/code-review",
     command="python scripts/run_tests.py --diff-file $WORK_DIR/sample.diff > out/test_summary.json",
     output_files=["out/test_summary.json"]
   )
   ```

Notes

- The first implementation keeps the core logic deterministic so dry-run mode can
  exercise the full pipeline without a real model API key.
- The skill complements the main agent orchestrator rather than replacing it:
  the agent owns task lifecycle, persistence, reporting, and governance.
