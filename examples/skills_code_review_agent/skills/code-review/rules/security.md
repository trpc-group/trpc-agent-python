# Security rules

## Detection contract

| Rule ID | Reports when changed Python code contains | Severity | Heuristic confidence |
|---|---|---:|---:|
| `security.sql-fstring` | an SQL keyword in an interpolated f-string | high | 0.82 |
| `security.sql-interpolation` | complete-file AST confirms SQL built with `+`, `.format()`, or `%` | high | AST only |
| `security.subprocess-shell-true` | a supported `subprocess` call with literal `shell=True` | high | 0.85 |
| `security.subprocess-shell-command` | `subprocess.getoutput/getstatusoutput`, which always invoke a shell | high | 0.85 |
| `security.dynamic-eval` | direct `eval`, `builtins.eval`, static `getattr(builtins, "eval")`, or an explicit import alias | critical | 0.85 where line-detectable |
| `security.dynamic-exec` | direct `exec`, `builtins.exec`, static `getattr(builtins, "exec")`, or an explicit import alias | critical | 0.85 where line-detectable |
| `security.os-system` | `os.system` or an explicit `from os import system` alias | high | 0.85 where line-detectable |
| `security.os-popen` | `os.popen` or an explicit import alias | high | 0.85 |

When a complete syntax-valid Python file is available, the AST confirmation
rule reports these IDs at confidence 0.92. It resolves explicit module/import
aliases conservatively and stops when a parameter or assignment shadows the
name. Different detectors may therefore reach the same
`(file, line, category)`; host deduplication owns the final item.

## Scope and confidence

Heuristics inspect added new-side lines in `.py` files and mask ordinary
strings, comments, and triple-quoted text. SQL f-strings remain visible because
their interpolation is the behavior under review. AST analysis requires
`analysis_mode=ast_validated`; changed-line reviews report an AST node only when
its range intersects a changed line, while `full_file` snapshots may report any
line. Deleted Python code is outside this category.

Confidence describes detector precision, not exploitability. Severity describes
the potential impact if attacker-controlled data reaches the construct.

## Examples

### Reports

```python
query = f"SELECT * FROM users WHERE name = '{name}'"
query = "SELECT * FROM users WHERE id = " + user_id
subprocess.run(command, shell=True)
subprocess.getoutput(command)
result = eval(payload)
builtins.eval(payload)
exec(compiled_code)
os.system(command)
os.popen(command)
```

### Stays quiet

```python
query = "SELECT * FROM users WHERE name = ?"
cursor.execute(query, (name,))
subprocess.run(["tool", "--mode", mode], check=True)
client.getoutput(command)
message = "eval(payload) is forbidden in this project"
# os.system(command)
```

## Remediation

Use parameterized SQL, argv-based subprocess calls with the shell disabled,
and an explicit allowlisted dispatcher or strict parser in place of dynamic
code execution. Validate untrusted values at the boundary even after replacing
the dangerous sink.

## Blind spots

These rules do not perform general taint tracking or authorization analysis.
Runtime alias assignments such as `evaluate = eval`, non-literal propagation
such as `shell=use_shell`, wrapper functions, dynamically assembled attribute
names, SQL assembled across multiple statements, and command injection inside
an otherwise argv-based tool may escape detection. A quiet review means only
that the supported syntax did not match.
