# Code Review Rules

This document outlines the standard code review rules utilized by the Code Review Agent.

## 1. Security Risks
- **Shell Injections**: Avoid using `subprocess.run(..., shell=True)` with unvalidated user input or string formatting.
- **Unsafe Deserialization**: Avoid using Python's `pickle` on untrusted inputs. Use safe parsers like `json`.

## 2. Async Errors
- **Coroutine Not Awaited**: Every async function/coroutine call must be prefixed with `await`, `asyncio.create_task()`, or processed via an event loop.
- **Blocking Calls**: Do not block the asyncio event loop with blocking synchronous operations (e.g. `time.sleep()`, synchronous network requests). Use `await asyncio.sleep()` instead.

## 3. Resource Leaks
- **File Connection Lifecycles**: Always open files using context managers (`with open(...) as f:`).
- **Socket / Database Connection Leak**: Ensure network connections, database connections, and sockets are closed or released in a `finally` block or handled via context managers.

## 4. Database Transaction and Connection Lifecycle
- **Unmanaged Transactions**: Database transactions must be managed properly. Commits/rollbacks should be inside try/except/finally blocks or managed by session contexts.
- **Leaked Sessions**: Avoid creating new DB sessions without closing them or using a context manager.

## 5. Sensitive Information Leaks
- **Credentials/Keys**: Do not embed raw API keys, bearer tokens, or database passwords in source code. Any matches like `api_key = "..."` or `password = "..."` must be flagged and redacted.

## 6. Missing Unit Tests
- **Test Coverage**: Any new classes or functions added to source files (e.g. in `src/` or core modules) should have corresponding tests added or modified in the `tests/` directory.
