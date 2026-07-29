# secrets rules

Regex-first check (`checks/check_secrets.py`, `CATEGORY="secrets"`): credentials hide in
every file type, so **all** languages are scanned line by line — python, yaml, shell, sql
and `other` alike; only `binary` and `deleted` files are skipped. Python additionally gets
an AST pass for SECRET012 so real assignments are separated from incidental text. Findings
are only ever raised on candidate (changed) lines, and the evidence never contains the
secret itself (see [Redaction](#redaction)).

## Rules

| ID | What | Severity | Precision | Confidence |
|---|---|---|---|---|
| SECRET001 | AWS access key ID | critical | high | high |
| SECRET002 | AWS secret access key (40-char blob + context) | critical | high | medium |
| SECRET003 | GitHub token | critical | high | high |
| SECRET004 | GitLab personal access token | critical | high | high |
| SECRET005 | Slack token | critical | high | high |
| SECRET006 | Stripe live key | critical | high | high |
| SECRET007 | Google API key | critical | high | high |
| SECRET008 | OpenAI / Anthropic style key | critical | high | high |
| SECRET009 | PEM private key header | critical | high | high |
| SECRET010 | JSON Web Token | high | high | high |
| SECRET011 | Password embedded in a URL | critical | high | high |
| SECRET012 | Sensitive variable = hardcoded literal | high | high * | high * |
| SECRET013 | High-entropy string near credential context | medium | low | low |

\* SECRET012: `high`/`high` from the AST, `high`/`medium` for the structural
`key: value` / `KEY=value` regex on non-Python files, `low`/`low` on the AST-failed
diff-only fallback for Python.

### SECRET001–SECRET011 — token patterns

Exact patterns (verbatim from the implementation):

| ID | Pattern |
|---|---|
| SECRET001 | `\bAKIA[0-9A-Z]{16}\b` |
| SECRET002 | `(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])` (see note below) |
| SECRET003 | `\b(?:(?:ghp\|gho\|ghu\|ghs\|ghr)_[A-Za-z0-9]{36}\|github_pat_[A-Za-z0-9_]{22,})\b` |
| SECRET004 | `\bglpat-[A-Za-z0-9_-]{20,}\b` |
| SECRET005 | `\bxox[abprs]-[A-Za-z0-9-]{10,}\b` |
| SECRET006 | `\b[srp]k_live_[A-Za-z0-9]{20,}\b` |
| SECRET007 | `\bAIza[0-9A-Za-z_-]{35}\b` |
| SECRET008 | `\bsk-(?:proj-\|ant-)?[A-Za-z0-9_-]{20,}\b` |
| SECRET009 | `-----BEGIN [A-Z ]*PRIVATE KEY-----` |
| SECRET010 | `\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b` |
| SECRET011 | `\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:(?P<pw>[^@\s/]{4,})@` |

SECRET002 only fires on lines whose text matches `(?i)aws|secret` (the 40-char blob alone
is too generic); `\b` cannot delimit `/` `+` `=`, hence the explicit class lookarounds.
SECRET010 is `high` (not critical): a JWT is frequently short-lived, but it embeds claims
and is replayable until expiry. SECRET011 masks only the password part (`***`), the rest of
the URL stays visible so the reviewer can locate the DSN.

### SECRET012 — sensitive variable assigned a hardcoded literal

A binding whose **name** matches

```
(?i)(?<![a-z0-9])(password|passwd|pwd|secret|token|api_?key|access_?key|private_?key|auth)(?![a-z0-9])
```

and whose **value** is a string literal of ≥ 8 characters. `_` counts as a word separator
(unlike `\b`), so `AUTH_TOKEN` / `x_api_key` match while `author`, `oauth_provider`,
`passwords`, `keyboard` do not.

* **Python**: AST engine — plain and annotated assignments (including `self.attr = ...`),
  keyword arguments (`connect(password="...")`) and literal dict entries
  (`{"api_key": "..."}`). Values that are not `Constant` strings (e.g.
  `os.environ[...]`, f-strings) can never match.
* **yaml / shell / other**: structural line regex `name[:=] value` (tolerates `export`,
  yaml list dashes, quoted JSON keys). Commented-out keys never match (the name must start
  the statement) — but the SECRET001–011 token patterns still scan comment text on
  purpose: a pasted live key in a comment is still a leak.
* **Python whose AST fails** (diff-only gap reconstruction, syntax error): same line regex,
  degraded honestly to precision `low`, confidence `low`. Never raises.

### SECRET013 — high-entropy fallback net

Catches keys with no known prefix: a token `[A-Za-z0-9+/=_-]{20,}` with Shannon entropy
> 4.5 bits/char, on a line whose text matches `(?i)key|secret|token|credential`.
Mathematically the threshold needs ≥ 23 mostly-distinct characters, so ordinary
identifiers and English words stay below it. This is the noisy net at the bottom:
severity `medium`, precision/confidence `low`, at most one finding per line, and no
`fix_snippet` (too uncertain to auto-suggest a rewrite).

## Suppression order — one secret, one finding

Token rules run per line in a fixed priority order (specific prefixes first, then the URL
rule, the generic 40-char AWS blob last); a later rule never re-reports characters already
claimed by an earlier rule on the same line (span-overlap check). SECRET012 skips lines
that already carry a token finding; SECRET013 only fires on lines with no other secrets
finding at all. So `AWS_ACCESS_KEY_ID = "AKIA..."` is exactly one SECRET001, not
SECRET001 + SECRET012 + SECRET013.

## Shared allowlist (any hit skips the finding)

Applied to the matched value **and** the assignment/key name on the line, for every rule:

* the official AWS documentation sample credentials
  `AKIAIOSFODNN7EXAMPLE` and `wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY`;
* placeholder vocabulary `(?i)example|sample|dummy|placeholder|fake|test|changeme|your[_-]`
  in the value or the variable name (this also silences `sk_test_...` style non-live keys);
* whole-value placeholder shapes: `<...>`, `${...}`, `{{...}}`, `$VAR`, `$(cmd)`, `***`,
  `xxx...`;
* values read from the environment: `os.environ[...]` / `getenv(...)`;
* all-same-character values (`aaaaaaaaaaaa`, `000000000000`).

SECRET012 additionally ignores values that cannot be credentials:

* pure numbers / booleans / null (`auth_timeout: 30000000`);
* bare URLs without userinfo (`token_url: https://login.corp.com/token` — endpoints are
  not secrets; credential-bearing URLs are SECRET011's job);
* filesystem paths (`key_file: /etc/ssl/server.key` names a key, it is not one);
* values containing whitespace (prose, not a credential token);
* `=`/comparison fragments picked up by the loose `[:=]` split.

Known trade-off (documented, accepted): the word allowlist suppresses a *real* key whose
value or name happens to contain `test`/`sample`/... — false negatives are preferred over
noise here, and the LLM review pass can still catch those.

## Redaction

The check must not leak what it finds, independent of the host's second Redactor layer:

* evidence and `fix_snippet.before` keep only the **first 4 characters** of the secret:
  `AKIA…<redacted>`;
* for SECRET011 the password inside the URL becomes `***`;
* `fix_snippet.after` suggests the language-appropriate environment lookup
  (`os.environ["..."]` / `${VAR}` / `"${VAR}"`), never the value.

## Diff-only robustness

Line scanning is identical in repo and diff-only mode (gap-reconstructed blank lines simply
match nothing). For `content_complete=False` Python files the SECRET012 AST engine degrades
to the line regex as described above; nothing in the module raises on partial content.
