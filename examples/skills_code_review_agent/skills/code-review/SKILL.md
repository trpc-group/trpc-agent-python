---
name: code-review
description: Review normalized source-code changes for security, reliability, resource, testing, secret, and database risks.
rules:
  - rules/security.md
  - rules/async-resource.md
  - rules/testing-db.md
  - rules/runtime.md
scripts:
  - scripts/rule_runner.py
outputs:
  schema_version: code-review.rules.v1
---

# Code Review Skill

Use this Skill to inspect normalized code changes produced by the example input
parser. The primary input is an `InputSummary` JSON document containing changed
files, hunks, context lines, added/deleted line numbers, candidate review lines,
parser diagnostics, and a stable input digest.

## Inputs

- `parsed_input.json`: normalized input summary for a diff, fixture, or Git worktree.
- `skill_manifest.json`: manifest produced by the local Skill loader.

Rule scripts must analyze only the supplied structured inputs. They must not
scan arbitrary host paths, read credentials, call the network, or infer source
state outside the review workspace prepared by the caller.

## Rules

Review guidance lives under `rules/`:

- `rules/security.md`
- `rules/async-resource.md`
- `rules/testing-db.md`
- `rules/runtime.md`

Each rule document uses rule intent, examples, and trigger conditions so later
deterministic checks and human reviewers share the same vocabulary.

## Script Entry

The script entrypoint is `scripts/rule_runner.py` from `__BASE_DIR__`. It accepts
JSON paths with `--input` and `--manifest`, and can write JSON to `--output`.
Governance and sandbox layers must approve runtime, command, working directory,
environment variables, timeout, and output limits before executing it.

## Output

The rule runner returns structured JSON with:

- `schema_version`
- `skill_name`
- `findings`
- `diagnostics`

Findings include `severity`, `category`, `file`, `line`, `title`, `evidence`,
`recommendation`, `confidence`, `source`, and `fingerprint`.
