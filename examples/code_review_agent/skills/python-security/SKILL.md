---
name: python-security
description: Review changed Python code for exploitable security defects involving trust boundaries, injection, authentication, authorization, secrets, unsafe deserialization, path handling, network access, and sandbox escape. Use when a Python diff processes external input, executes commands, accesses files or databases, handles credentials, exposes an API, or changes security-sensitive behavior.
---

# Review Python Security

Require an explicit source, dangerous operation, and missing or ineffective control.

## Trace trust boundaries

1. Identify attacker-controlled values from requests, events, files, environment variables, model output, or repository content.
2. Trace the value into a security-sensitive sink.
3. Verify that validation, encoding, parameterization, authorization, or isolation occurs before the sink.
4. Report the exact changed statement from the supplied `ADDED LINE MAP` that introduces or
   exposes the exploitable path; never substitute a nearby commentable line.

## Check high-value sinks

- Shell/process execution, `eval`, `exec`, dynamic imports, or template evaluation.
- SQL, query languages, regular expressions, and structured command construction.
- Filesystem paths, archives, symlinks, uploads, and path traversal.
- Pickle/YAML/object deserialization or dynamic class construction.
- Authentication, authorization, tenant scoping, and ownership checks.
- Tokens, passwords, keys, logs, traces, and error responses.
- HTTP fetches, redirects, callback URLs, SSRF, and unrestricted egress.
- Docker/socket access, privileged mounts, writable host paths, and disabled isolation.
- Weak randomness, signature verification, TLS validation, and unsafe defaults.

## Calibrate severity

- `critical`: direct compromise with broad impact and little precondition.
- `high`: realistic exploit causing code execution, auth bypass, secret disclosure, or major data access.
- `medium`: meaningful exposure requiring additional conditions.
- `low`: defense-in-depth weakness with limited direct exploitability.

Do not infer that any string is attacker controlled without evidence. Do not report a scanner
diagnostic when the visible code parameterizes, validates, or otherwise neutralizes the input.
