# Code review rules

The scanner applies deterministic, explainable checks. A match is a candidate,
not proof; confidence reflects expected precision.

## Security

- Flag added dynamic execution such as `eval`, `exec`, `pickle.loads`, unsafe
  YAML loading, or subprocess calls with `shell=True`.
- Flag SQL assembled from untrusted strings.
- Do not flag safe literal parsing or parameterized SQL.

## Async errors

- Flag a coroutine call assigned or invoked in `async def` without `await`
  when the changed line clearly identifies an async API.
- Flag blocking `time.sleep`, synchronous HTTP, or blocking subprocess calls
  added to an async function.
- Do not guess when surrounding context cannot establish async execution.

## Resource leaks

- Flag added `open`, process, lock, cursor, or connection acquisition without a
  context manager or visible `try/finally` cleanup in the hunk.
- Do not flag `with`/`async with` ownership.

## Missing tests

- Flag production behavior changes when no test file appears in the same input.
- Emit one finding per changed production file, not per line.
- Documentation, fixture, generated, and configuration-only changes are exempt.

## Secret leaks

- Flag API keys, bearer tokens, private-key headers, URL credentials, and
  password assignments.
- Evidence must mask the value before leaving the scanner.
- Placeholder, example, environment lookup, and already-redacted values are
  exempt.

## Database lifecycle

- Flag transactions that return or raise without rollback/managed context.
- Flag connections or cursors acquired without close/context management.
- Flag commits inside exception handlers where rollback is expected.

## Severity and confidence

- `critical`: directly exploitable secret or command/code execution.
- `high`: likely security, transaction, or credential defect.
- `medium`: async/resource defect or important missing test.
- `low`: weak heuristic.
- Confidence below `0.70` is routed to warning and human review by the caller.
