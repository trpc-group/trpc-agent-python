# Resource Leak Rules

## RSC-001: Unclosed File Handles
- **Severity**: high
- **Pattern**: `open()` without context manager or explicit `.close()`
- **Detection**: `open\(` not followed by `with` in the same logical block
- **Recommendation**: Use `with open(...) as f:` context manager

## RSC-002: Unclosed HTTP Sessions
- **Severity**: high
- **Pattern**: `requests.Session()` or `aiohttp.ClientSession()` without close
- **Detection**: `requests\.Session\(\)`, `aiohttp\.ClientSession\(\)` without `with` or close
- **Recommendation**: Use `with requests.Session() as s:` or `async with aiohttp.ClientSession() as s:`

## RSC-003: Database Connection Leaks
- **Severity**: critical
- **Pattern**: `pymysql.connect()`, `sqlite3.connect()` without close/context
- **Detection**: `\.connect\(` (DB drivers) without `with` or `.close()`
- **Recommendation**: Use connection pooling or context managers

## RSC-004: Thread/Process Leaks
- **Severity**: medium
- **Pattern**: `threading.Thread()` started without join, subprocess without wait
- **Detection**: `\.start\(\)` without nearby `\.join\(\)`
- **Recommendation**: Always join threads or use `concurrent.futures`
