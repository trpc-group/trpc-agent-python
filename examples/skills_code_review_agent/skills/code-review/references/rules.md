# Review rules

| Rule | Category | Intent |
|---|---|---|
| SEC001 | security | Detect command execution, unsafe deserialization, and dynamic evaluation. |
| ASYNC001 | async_error | Detect blocking sleep inside asynchronous functions and discarded tasks. |
| RES001 | resource_leak | Detect files or HTTP responses created without bounded lifetime or timeout. |
| DB001 | database_lifecycle | Detect database connections without context management or explicit close. |
| SECRET001 | sensitive_information | Detect credentials committed in added lines. |
| TEST001 | test_missing | Flag production changes with no corresponding test change for human review. |

Rules inspect added diff lines and nearby hunk context. Findings at confidence below 0.75 belong in `warnings`; they must not be promoted automatically. Extend rules with a stable ID, narrow evidence pattern, recommendation, and paired positive/negative fixtures.
