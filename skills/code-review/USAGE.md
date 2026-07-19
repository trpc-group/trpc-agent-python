# Code Review Skill Usage

## When To Invoke

Invoke `code-review` when you need:

- structured review over a unified diff
- deterministic risk detection in dry-run or fake-model mode
- a controlled script-based review step before writing a final report
- reusable review logic that should stay outside the main agent prompt

## When Not To Invoke

Do not invoke this skill for:

- trivial file reads or one-line code questions
- full repository security auditing outside the changed diff scope
- governance decisions that belong to `Filter`
- persistence, report writing, or task lifecycle management

## Recommended Pattern

1. Normalize the input diff or repo change set in the main agent.
2. Load the `code-review` skill.
3. Run one or more scripts through `skill_run`.
4. Merge script output with deterministic rule-engine findings.
5. Apply redaction before persistence and reporting.

## Integration Boundary

This skill owns:

- reusable review instructions
- rule documentation
- script-level deterministic checks

The main agent still owns:

- task orchestration
- filter decisions
- sandbox policy
- storage
- final report generation
