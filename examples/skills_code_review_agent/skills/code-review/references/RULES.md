# Review Rules

Apply these rules only when the changed code provides concrete evidence. Use
unchanged code solely to confirm lifecycle, ownership, or call-site behavior.

## Security

Script: `scripts/review_security.py`

- Flag command, SQL, template, or path construction that allows untrusted input
  to cross an execution boundary without validation or parameterization.
- Flag authorization checks that are removed, bypassed, or performed after a
  privileged operation.

## Async correctness

Script: `scripts/review_async.py`

- Flag missing `await`, orphaned tasks, blocking calls in async paths, and
  cancellation handling that leaves shared state inconsistent.

## Resource lifecycle

Script: `scripts/review_resources.py`

- Flag files, locks, sockets, processes, streams, or executors that are acquired
  without deterministic cleanup on success and failure paths.

## Database lifecycle

Script: `scripts/review_database.py`

- Flag connections, cursors, sessions, or transactions that can leak, remain
  uncommitted, or skip rollback/close after exceptions.

## Test coverage

Script: `scripts/review_tests.py`

- Flag material behavior changes with no focused test when the risk cannot be
  covered by an existing test. State the missing scenario; do not demand tests
  for comments, formatting, or mechanically equivalent changes.

## Sensitive information

Script: `scripts/review_secrets.py`

- Flag hard-coded credentials, tokens, private keys, passwords, or production
  endpoints. Never copy the full value into evidence; retain only a redacted
  prefix and suffix when identification is necessary.

## Confidence and severity

- Use `critical` or `high` only for reachable issues with strong evidence and
  material impact.
- Put confidence below `0.70` in `warnings` or `needs_human_review`.
- Deduplicate identical `(file, line, category)` findings and keep the entry
  with the strongest evidence.
