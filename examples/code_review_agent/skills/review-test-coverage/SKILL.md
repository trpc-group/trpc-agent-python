---
name: review-test-coverage
description: Review a code diff for important missing or ineffective tests around changed behavior, regressions, error paths, boundaries, security controls, and compatibility guarantees. Use when production behavior changes, a defect is fixed, a new branch or public contract is added, or existing tests are modified without clearly exercising the risky path.
---

# Review Test Coverage

Request tests only for behavior whose failure would matter and whose coverage gap is visible.

## Derive test obligations

1. List observable behavior introduced or changed by the diff.
2. Identify the highest-risk success, failure, boundary, and compatibility paths.
3. Match visible tests to those obligations.
4. Report a gap only when no visible assertion exercises a material changed path.
5. Point to the exact production statement in the supplied `ADDED LINE MAP` that creates the
   untested obligation, not a nearby added line or an arbitrary test file line.

## Prioritize

- Security checks and authorization denials.
- Regression fixes that lack a reproducing test.
- New exception, rollback, timeout, retry, and cleanup paths.
- Zero, empty, maximum, malformed, and concurrent inputs.
- Schema, protocol, persistence, or public API compatibility.
- Failure behavior of external dependencies.

## Avoid weak requests

Do not ask for:

- Coverage of trivial getters or declarative constants.
- Tests merely to increase a percentage.
- Duplicates of an existing test visible in the diff.
- Tests for speculative behavior outside the available context.

Use `medium` for an untested path likely to regress a material behavior. Use `low` for a narrower
gap. Describe the input, action, and assertion required for one focused regression test.
