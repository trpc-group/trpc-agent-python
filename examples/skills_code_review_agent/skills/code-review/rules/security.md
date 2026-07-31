# Security And Secret Review Rules

## Dangerous Execution

Rule: changed code must not pass untrusted input into `eval`, `exec`, dynamic
imports, shell commands, deserializers, template rendering, or archive extraction
without clear validation and containment.

Example:

```python
subprocess.run("deploy " + request.args["target"], shell=True)
```

Trigger conditions:

- Changed lines introduce `shell=True`, string-built commands, `eval`, `exec`,
  `pickle.loads`, permissive YAML loading, or path-sensitive extraction.
- User, request, environment, database, queue, or file content reaches execution
  or filesystem APIs without validation.
- Evidence is present in changed lines or immediate hunk context.

## Path And Permission Boundaries

Rule: changed code must keep reads, writes, extraction, and deletion inside an
approved workspace or application-owned directory.

Example:

```python
target = Path("/tmp/uploads") / request.json["name"]
target.unlink()
```

Trigger conditions:

- New path joins use user-controlled segments without normalization checks.
- Absolute paths, `..`, recursive deletion, chmod, chown, or world-writable
  temporary files are introduced.
- Review confidence should drop when the diff omits caller trust boundaries.

## Sensitive Information Disclosure

Rule: changed code and test data must not expose API keys, bearer tokens,
passwords, private keys, connection strings, cookies, or authorization headers.

Example:

```python
logger.info("connecting with password=%s", password)
API_KEY = "example-sensitive-api-key-value"
```

Trigger conditions:

- Credential-like literals appear in changed lines.
- Logs, exceptions, telemetry, reports, or database rows include secret-bearing
  variables or headers.
- Recommendations must preserve evidence without copying full secret values.
