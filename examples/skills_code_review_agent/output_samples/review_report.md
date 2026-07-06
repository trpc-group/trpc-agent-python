# Code Review Report

- Generated: 2026-07-05T15:24:57Z
- Dry run: True
- Diff file: `examples\skills_code_review_agent\fixtures\security.diff`
- Files scanned: 2
- Total findings: 7

## Severity Counts

- high: 5
- medium: 2

## Findings

| Severity | Category | Location | Title | Evidence | Recommendation |
| --- | --- | --- | --- | --- | --- |
| high | secret | app/payment.py:5 | Possible hardcoded secret, token, or password | `api_token = "<redacted:726a793d>"` | Move secrets to a secret manager or environment variable and rotate exposed values. |
| high | sql-injection | app/payment.py:6 | SQL query appears to be built with string interpolation or concatenation | `query = "SELECT * FROM users WHERE id = " + user_id` | Use parameterized queries or the database driver's bind parameter API. |
| medium | network-timeout | app/payment.py:8 | HTTP request is missing an explicit timeout | `response = requests.post("https://payments.example/charge", json={"user": user_id})` | Pass a bounded timeout, for example timeout=10, to avoid hanging workers. |
| medium | resource-lifecycle | app/payment.py:13 | File handle may not be closed on all paths | `key_file = open(path, encoding="utf-8")` | Use a context manager such as with open(...) as f to guarantee cleanup. |
| high | error-handling | app/payment.py:20 | Broad exception handler may hide failures | `except Exception:` | Catch the narrowest expected exception and log or re-raise unexpected failures. |
| high | secret | app/profile.py:10 | Possible hardcoded secret, token, or password | `password = "<redacted:22588986>"` | Move secrets to a secret manager or environment variable and rotate exposed values. |
| high | sql-injection | app/profile.py:11 | SQL query appears to be built with string interpolation or concatenation | `sql = f"UPDATE users SET name = '{name}'"` | Use parameterized queries or the database driver's bind parameter API. |
