---
name: review-maintainability
description: Review changed code for material maintainability risks that are likely to cause defects or costly future changes, such as duplicated policy, incompatible abstractions, hidden coupling, unbounded complexity, and misleading interfaces. Use for non-trivial diffs where long-term change safety matters; do not use for cosmetic style commentary.
---

# Review Maintainability

Report maintainability only when the diff creates a concrete future failure mode or makes a
required change unsafe across multiple locations.

## Evaluate the change boundary

1. Identify the behavior or policy being added.
2. Check whether the same rule is now encoded in multiple changed locations.
3. Check whether names, types, and interfaces still communicate the actual contract.
4. Check whether callers must know internal sequencing or representation details.
5. Explain the next realistic change that would break or require synchronized edits.
6. Locate the exact supporting statement in the supplied `ADDED LINE MAP`; do not attach the
   finding to a nearby added line.

## Report material risks

- Duplicated authorization, validation, serialization, or lifecycle policy.
- A public interface that accepts states the implementation cannot safely handle.
- Hidden global state or order-dependent initialization.
- Branching or nesting that obscures cleanup, errors, or invariants.
- Cross-layer coupling that bypasses the existing abstraction boundary.
- Configuration spread across code paths with conflicting defaults.
- Compatibility behavior implemented without a bounded removal or migration path.

## Avoid noise

Do not report naming, formatting, line length, minor duplication, or personal design preferences.
Do not request a broad refactor when a small local correction removes the concrete risk.
Default to `low`; use `medium` only when the design is already likely to cause incorrect changes.
