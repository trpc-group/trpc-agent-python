# Security Rule

Detects dangerous patterns in added Python source lines.

## Patterns

| Pattern | Severity | Confidence | Guidance |
|---------|----------|------------|----------|
| `eval()` | high | 0.9 | Avoid eval(); use ast.literal_eval or explicit parsing. |
| `exec()` | high | 0.9 | Avoid exec(); restructure to avoid dynamic execution. |
| `shell=True` | high | 0.85 | Pass an argument list and shell=False to avoid shell injection. |
| `pickle.load/loads()` | high | 0.8 | Never unpickle untrusted input; use JSON or a safe serializer. |
| `yaml.load()` without SafeLoader | medium | 0.75 | Use yaml.safe_load or pass Loader=yaml.SafeLoader. |
| SQL via f-string: `.execute(f"...")` | high | 0.85 | Use parameterized queries (placeholders) instead of string interpolation. |
| SQL via concatenation: `.execute("..." + ...` | high | 0.8 | Use parameterized queries (placeholders) instead of concatenation. |
| `os.system()` | medium | 0.7 | Use subprocess.run with an argument list. |

## Remediation

Each finding includes a specific recommendation. All security findings should be resolved
before merging; high-severity findings must not be deferred.
