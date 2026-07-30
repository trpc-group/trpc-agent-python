# Security Rules

## SEC-001: Hardcoded Secrets
- **Severity**: critical
- **Pattern**: API keys, passwords, tokens, secrets in source code
- **Detection**: `password\s*=\s*["'][^"']+["']`, `api_key\s*=\s*["'][^"']+["']`, `secret\s*=\s*["'][^"']+["']`
- **Recommendation**: Use environment variables or secret management services

## SEC-002: Command Injection
- **Severity**: critical
- **Pattern**: `shell=True` in subprocess calls, `os.system()`, `os.popen()`
- **Detection**: `shell\s*=\s*True`, `os\.system\(`, `os\.popen\(`
- **Recommendation**: Use `subprocess.run()` with argument lists and `shell=False`

## SEC-003: Unsafe Deserialization
- **Severity**: high
- **Pattern**: `pickle.loads()`, `yaml.load()` without SafeLoader
- **Detection**: `pickle\.loads?\(`, `yaml\.load\(` (without `Loader=yaml.SafeLoader`)
- **Recommendation**: Use `yaml.safe_load()` or `pickle` only with trusted data

## SEC-004: Dynamic Code Execution
- **Severity**: critical
- **Pattern**: `eval()`, `exec()`, `compile()` with user input
- **Detection**: `\beval\(`, `\bexec\(`
- **Recommendation**: Avoid dynamic code execution; use safe alternatives
