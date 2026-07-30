---
name: python-correctness
description: Review changed Python code for concrete behavioral defects, including invalid control flow, broken contracts, exception mistakes, state corruption, async/concurrency errors, resource leaks, and boundary-condition failures. Use when a diff contains Python source or tests and the task is to identify correctness regressions introduced by the change.
---

# Review Python Correctness

Review only behavior introduced or changed by the diff.

## Establish evidence

1. Trace changed inputs through changed branches and outputs.
2. Compare new behavior with nearby call sites, annotations, tests, and error handling visible in the diff.
3. Report an issue only when a concrete input or execution path demonstrates failure.
4. Point to the exact statement in the supplied `ADDED LINE MAP`. Never use a nearby added line
   merely because it is commentable.

## Check high-value failure modes

- Return values no longer match the visible contract or callers.
- Exceptions are swallowed, converted incorrectly, or raised after partial mutation.
- A new branch omits a required state transition, cleanup, or return.
- `None`, empty, zero, false, and boundary values take an invalid path.
- Mutable defaults, aliases, or shared state leak across calls.
- Async work is not awaited, cancellation is swallowed, or concurrent writes race.
- Files, locks, sessions, processes, or transactions are leaked on an error path.
- Indexing, iteration, timezone, encoding, or numeric assumptions fail at boundaries.

## Reject weak findings

Do not report:

- Pure style preferences.
- Hypothetical behavior requiring code not shown in the diff.
- A warning already disproved by visible guards or tests.
- An old defect on an unchanged line.

Assign `high` only when the changed path can corrupt data, break a primary flow, or fail broadly.
Use `medium` for reproducible functional defects and `low` for narrow edge cases.
