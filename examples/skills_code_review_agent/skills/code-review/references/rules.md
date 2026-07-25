# Review Rules

The deterministic scanner evaluates added lines only and uses hunk context to
avoid reporting lifecycle issues that are closed in the same change.

## Security

- Flag direct dynamic evaluation through `eval` or `exec`.
- Flag shell-enabled subprocess calls and command execution APIs.
- Flag SQL built through interpolation at the execution call site.
- Recommend fixed commands, argument arrays, strict parsers, or parameterized
  statements.

## Async Errors

- Flag detached `asyncio.create_task` or `ensure_future` calls whose handle is
  not retained.
- Flag broad exception handlers that silently discard failures as a
  low-confidence human-review item.
- Recommend structured task ownership and explicit error propagation.

## Resource Lifecycle

- Flag files, HTTP clients, and similar resources opened without a context
  manager or visible close operation in the hunk.
- Recommend `with` or `async with`, or a `try/finally` cleanup path.

## Database Lifecycle

- Flag database connections without a close operation or context manager.
- Flag transactions without a visible commit, rollback, or managed transaction
  scope.
- Recommend short scopes with rollback on every exceptional path.

## Test Coverage

- When a non-test source file receives at least six added lines and no test
  file changes, emit a medium-confidence review item.
- This heuristic is intentionally routed to human review when confidence drops
  below the configured threshold.

## Sensitive Data

- Flag API keys, access tokens, passwords, private keys, bearer tokens, cloud
  access keys, and common provider token prefixes.
- Evidence is redacted before it leaves the scanner. The host repeats
  redaction before report rendering and every database write.

## Confidence and Deduplication

Findings at or above `0.80` are included in the primary list. Lower-confidence
items are placed in warnings and `needs_human_review`. Findings are unique by
file, target line, and category; if two rules collide, the higher severity and
confidence result wins.
