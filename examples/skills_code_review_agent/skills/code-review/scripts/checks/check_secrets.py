# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""secrets: hardcoded credentials, API keys and key material in changed lines.

Unlike the AST-first categories, secrets hide in *any* file type, so this
check is regex-first on purpose: every non-binary, non-deleted file
(python/yaml/shell/sql/other) is scanned line by line, and only lines inside
``candidate_lines`` (the actual change) can be reported.  Python files get an
additional AST pass for SECRET012 so that real assignments are separated from
incidental mentions.

Rules (severity/precision)
--------------------------
SECRET001  AWS access key ID (``AKIA...``)                        critical/high
SECRET002  AWS secret access key: 40-char base64-ish blob on a
           line whose text mentions aws|secret                    critical/high
SECRET003  GitHub token (ghp_/gho_/ghu_/ghs_/ghr_/github_pat_)    critical/high
SECRET004  GitLab personal access token (glpat-...)               critical/high
SECRET005  Slack token (xox[abprs]-...)                           critical/high
SECRET006  Stripe live key (sk|rk|pk_live_...)                    critical/high
SECRET007  Google API key (AIza...)                               critical/high
SECRET008  OpenAI / Anthropic style key (sk-, sk-proj-, sk-ant-)  critical/high
SECRET009  PEM private key header                                 critical/high
SECRET010  JSON Web Token (three base64url segments)              high/high
SECRET011  password embedded in a URL userinfo section            critical/high
SECRET012  sensitive variable name assigned a hardcoded string
           literal: AST for python, ``key: value`` / ``KEY=value``
           regex for yaml/shell/other                             high/high
SECRET013  high-entropy string (len>=20, shannon entropy > 4.5)
           on a line mentioning key|secret|token|credential;
           fallback net, confidence=low                           medium/low

Suppression order (one secret, one finding)
-------------------------------------------
Token rules run per line in a fixed priority order (specific prefixes first,
the generic 40-char AWS blob last); a later rule never re-reports characters
already claimed by an earlier rule on the same line (span overlap check).
SECRET012 skips lines that already carry a token finding, and SECRET013 only
fires on lines with no other secrets finding at all.

Shared allowlist -- patterns deliberately NOT reported
------------------------------------------------------
* the official AWS documentation sample credentials
  ``AKIAIOSFODNN7EXAMPLE`` / ``wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY``;
* value or variable name containing example / sample / dummy / placeholder /
  fake / test / changeme / your[_-] (case-insensitive);
* whole-value placeholder shapes: ``<...>``, ``${...}``, ``{{...}}``,
  ``$VAR``, ``$(cmd)``, ``***``, ``xxx...``, and any all-same-character value;
* values read from the environment (``os.environ[...]`` / ``getenv(...)``);
* SECRET012 additionally ignores values that are pure numbers / booleans,
  bare URLs without userinfo credentials (endpoints are not secrets;
  credential-bearing URLs belong to SECRET011), filesystem paths
  (``key_file: /etc/ssl/server.key``), values containing whitespace (prose,
  not credentials) and ``=``/comparison fragments; its regex form only
  matches real ``name[:=] value`` lines, so commented-out config keys stay
  silent (prefix token rules still scan comment text on purpose: a pasted
  live key in a comment is still a leak).

Redaction
---------
Evidence and fix snippets never contain the secret itself: only the first 4
characters are kept (``AKIA…<redacted>``), and the password part of a
SECRET011 URL is replaced by ``***``.  The host applies a second Redactor
layer on top of this, but the check must not leak on its own.

Diff-only robustness
--------------------
For ``content_complete=False`` python files ``parse_ast`` may fail on the
gap-reconstructed text; SECRET012 then degrades to the per-line key/value
regex with precision=low and confidence=low.  Nothing in this module raises
on partial content: all scanning is per changed line and bounds-checked.
"""

from __future__ import annotations

import ast
import math
import re
from collections import Counter

from checks.common import FileCtx, make_finding

CATEGORY = "secrets"

# ---------------------------------------------------------------------------
# shared allowlist
# ---------------------------------------------------------------------------

#: canonical documentation credentials (AWS docs) -- always safe to publish
_ALLOW_LITERALS = frozenset({
    "AKIAIOSFODNN7EXAMPLE",
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
})
#: placeholder vocabulary; a hit in the value OR the variable name skips the finding
_ALLOW_WORD_RE = re.compile(r"(?i)example|sample|dummy|placeholder|fake|test|changeme|your[_-]")
#: whole-value placeholder shapes: <...>, ${...}, {{...}}, $VAR, $(cmd), ***, xxx...
_ALLOW_SHAPE_RES = (
    re.compile(r"^<[^<>]*>$"),
    re.compile(r"^\$\{[^{}]*\}$"),
    re.compile(r"^\{\{.*\}\}$"),
    re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*$"),
    re.compile(r"^\$\([^()]*\)$"),
    re.compile(r"^\*{3,}$"),
    re.compile(r"(?i)^x{3,}$"),
)
#: values that come from the environment, not from the source text
_ENV_LOOKUP_RE = re.compile(r"(?i)\bos\s*\.\s*environ\b|\bgetenv\s*\(")


def _is_allowlisted(value: str, var_name: str = "") -> bool:
    """True when the candidate secret is a known-safe placeholder (module docstring)."""
    if not value:
        return True
    if value in _ALLOW_LITERALS:
        return True
    if _ALLOW_WORD_RE.search(value):
        return True
    if var_name and _ALLOW_WORD_RE.search(var_name):
        return True
    for shape in _ALLOW_SHAPE_RES:
        if shape.match(value):
            return True
    if _ENV_LOOKUP_RE.search(value):
        return True
    if len(set(value)) == 1:  # "aaaaaaaa...", "00000000..." style fillers
        return True
    return False


# ---------------------------------------------------------------------------
# helpers shared by all rules
# ---------------------------------------------------------------------------

#: ``name = value`` / ``key: value`` head, tolerant of yaml list dashes,
#: shell ``export``, and quoted JSON-ish keys.
_KV_PREFIX = (r"^\s*(?:-\s+)?(?:export\s+|set\s+)?[\"']?"
              r"(?P<name>[A-Za-z_][A-Za-z0-9_.\-]*)[\"']?\s*[:=]\s*")
_KV_NAME_RE = re.compile(_KV_PREFIX)
_KV_RE = re.compile(_KV_PREFIX + r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s#]+)")

#: sensitive identifier vocabulary for SECRET012.  ``_`` counts as a word
#: separator (unlike ``\b``) so AUTH_TOKEN / x_api_key match while author /
#: oauth_provider / passwords / keyboard do not.
_SENSITIVE_NAME_RE = re.compile(
    r"(?i)(?<![a-z0-9])(?:password|passwd|pwd|secret|token|api_?key|access_?key|private_?key|auth)(?![a-z0-9])")

#: obviously-not-a-credential values for SECRET012 (numbers, booleans, null)
_NON_SECRET_VALUE_RE = re.compile(r"(?i)^(?:[0-9][0-9_.,eE+\-]*|true|false|null|none|yes|no|on|off)$")
_BARE_URL_RE = re.compile(r"(?i)^[a-z][a-z0-9+.\-]*://")
_PATH_LIKE_RE = re.compile(r"^(?:[A-Za-z]:\\|~?/|\.{1,2}/)")


def _unquote(raw: str) -> str:
    """Strip one layer of matching single/double quotes."""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def _extract_var_name(line: str) -> str:
    """Assignment/key name at the start of the line, "" when there is none."""
    m = _KV_NAME_RE.match(line)
    return m.group("name") if m else ""


def _looks_like_secret_value(value: str) -> bool:
    """SECRET012 value filter: keep only single-token, credential-shaped literals."""
    if len(value) < 8:
        return False
    if re.search(r"\s", value):
        return False  # prose / composite headers, not a credential token
    if value[0] in "=<>!":
        return False  # comparison fragment picked up by the loose [:=] split
    if _NON_SECRET_VALUE_RE.match(value):
        return False  # timeouts, ports, feature flags
    if _BARE_URL_RE.match(value) and "@" not in value:
        return False  # plain endpoint URL; credential URLs are SECRET011's job
    if _PATH_LIKE_RE.match(value):
        return False  # key_file: /etc/ssl/server.key names a path, not a key
    return True


def _redact_token(token: str) -> str:
    """First 4 characters only; the rest never reaches evidence or snippets."""
    return token[:4] + "…<redacted>"


def _overlaps(spans, span) -> bool:
    start, end = span
    return any(start < ce and cs < end for cs, ce in spans)


def _shannon_entropy(text: str) -> float:
    """Shannon entropy in bits per character."""
    if not text:
        return 0.0
    total = len(text)
    return -sum((n / total) * math.log2(n / total) for n in Counter(text).values())


def _env_name(var_name: str, default: str) -> str:
    """UPPER_SNAKE environment variable name derived from the assignment name."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", var_name or "").strip("_").upper()
    return cleaned or default


def _fix_after(ctx: FileCtx, var_name: str, default_env: str) -> str:
    """Language-appropriate replacement that reads the value from the environment."""
    env = _env_name(var_name, default_env)
    ref = var_name or env.lower()
    if ctx.language == "python":
        return f'{ref} = os.environ["{env}"]'
    if ctx.language == "yaml":
        return f"{ref}: ${{{env}}}"
    if ctx.language == "shell":
        return f'{ref}="${{{env}}}"'
    return f"{ref} = ${{{env}}}  # injected from the environment at deploy time"


# ---------------------------------------------------------------------------
# SECRET001..SECRET011: token pattern rules
# ---------------------------------------------------------------------------

_ROTATE = ("Remove the credential from source, rotate/revoke it immediately, and load it at "
           "runtime from the environment or a secret manager.")

#: scan order = suppression priority: specific prefixes first, then the URL
#: rule, then the generic 40-char AWS blob so it never re-reports a token a
#: more specific rule already claimed on the same line.
_TOKEN_RULES = (
    {
        "id":
        "SECRET001",
        "title":
        "Hardcoded AWS access key ID",
        "pattern":
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "severity":
        "critical",
        "confidence":
        "high",
        "env":
        "AWS_ACCESS_KEY_ID",
        "recommendation":
        "Deactivate and rotate the key in IAM immediately, audit CloudTrail for misuse, "
        "then read it from the environment or a secret manager.",
    },
    {
        "id":
        "SECRET003",
        "title":
        "Hardcoded GitHub token",
        "pattern":
        re.compile(r"\b(?:(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,})\b"),
        "severity":
        "critical",
        "confidence":
        "high",
        "env":
        "GITHUB_TOKEN",
        "recommendation":
        "Revoke the token in GitHub settings, rotate dependent automation, and inject it "
        "via CI/environment secrets.",
    },
    {
        "id": "SECRET004",
        "title": "Hardcoded GitLab personal access token",
        "pattern": re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
        "severity": "critical",
        "confidence": "high",
        "env": "GITLAB_TOKEN",
        "recommendation": _ROTATE,
    },
    {
        "id": "SECRET005",
        "title": "Hardcoded Slack token",
        "pattern": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
        "severity": "critical",
        "confidence": "high",
        "env": "SLACK_TOKEN",
        "recommendation": _ROTATE,
    },
    {
        "id":
        "SECRET006",
        "title":
        "Hardcoded Stripe live key",
        "pattern":
        re.compile(r"\b[srp]k_live_[A-Za-z0-9]{20,}\b"),
        "severity":
        "critical",
        "confidence":
        "high",
        "env":
        "STRIPE_API_KEY",
        "recommendation":
        "Roll the key in the Stripe dashboard (live keys move real money) and load it "
        "from a secret manager.",
    },
    {
        "id": "SECRET007",
        "title": "Hardcoded Google API key",
        "pattern": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
        "severity": "critical",
        "confidence": "high",
        "env": "GOOGLE_API_KEY",
        "recommendation": _ROTATE,
    },
    {
        "id": "SECRET008",
        "title": "Hardcoded OpenAI/Anthropic API key",
        "pattern": re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b"),
        "severity": "critical",
        "confidence": "high",
        "env": "LLM_API_KEY",
        "recommendation": _ROTATE,
    },
    {
        "id":
        "SECRET009",
        "title":
        "Private key material committed",
        "pattern":
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        "severity":
        "critical",
        "confidence":
        "high",
        "env":
        "PRIVATE_KEY",
        "recommendation":
        "Treat the key as compromised: remove it from source (and git history), reissue "
        "the key pair, and load key material from a file path or secret manager.",
    },
    {
        "id":
        "SECRET010",
        "title":
        "Hardcoded JWT",
        "pattern":
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"),
        "severity":
        "high",
        "confidence":
        "high",
        "env":
        "JWT_TOKEN",
        "recommendation":
        "JWTs embed claims and are often replayable until expiry: invalidate the token, "
        "shorten its TTL, and mint tokens at runtime instead of committing them.",
    },
    {
        "id":
        "SECRET011",
        "title":
        "Credentials embedded in URL",
        # user part excludes '@' (spec: [^/\s:]+) so redaction spans stay sane
        "pattern":
        re.compile(r"\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:(?P<pw>[^@\s/]{4,})@"),
        "severity":
        "critical",
        "confidence":
        "high",
        "env":
        "DB_PASSWORD",
        "recommendation":
        "Strip the password out of the URL (it leaks into logs, shell history and error "
        "messages), rotate it, and splice it in from the environment at runtime.",
    },
    {
        "id":
        "SECRET002",
        "title":
        "Possible hardcoded AWS secret access key",
        # \b breaks around '/' and '+', so use explicit class lookarounds
        "pattern":
        re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"),
        "context":
        re.compile(r"(?i)aws|secret"),
        "severity":
        "critical",
        "confidence":
        "medium",  # 40-char blob + context is strong but not exact
        "env":
        "AWS_SECRET_ACCESS_KEY",
        "recommendation":
        "Rotate the AWS secret key immediately, audit CloudTrail for misuse, and load it "
        "from the environment or a secret manager.",
    },
)


def _token_finding(ctx: FileCtx, rule: dict, line_no: int, text: str, m: re.Match, var_name: str) -> dict:
    """Build one finding for a token-rule match with the secret redacted."""
    if rule["id"] == "SECRET011":
        # spec: only the password part is masked, the rest of the URL stays
        redacted = text[:m.start("pw")] + "***" + text[m.end("pw"):]
        after = (text[:m.start("pw")] + "${" + rule["env"] + "}" + text[m.end("pw"):]).strip()
    else:
        redacted = text[:m.start()] + _redact_token(m.group(0)) + text[m.end():]
        after = _fix_after(ctx, var_name, rule["env"])
    before = redacted.strip()
    return make_finding(
        rule_id=rule["id"],
        category=CATEGORY,
        severity=rule["severity"],
        file=ctx.path,
        line=line_no,
        title=rule["title"],
        evidence=before,
        recommendation=rule["recommendation"],
        confidence=rule["confidence"],
        precision="high",
        fix_snippet={
            "before": before,
            "after": after
        },
    )


def _scan_token_rules(ctx: FileCtx, claimed_spans: dict) -> list[dict]:
    """Run SECRET001..SECRET011 over every changed line of one file."""
    findings: list[dict] = []
    for line_no in sorted(ctx.candidate_lines):
        text = ctx.line_text(line_no)
        if len(text) < 8:  # nothing token-sized fits
            continue
        var_name = _extract_var_name(text)
        for rule in _TOKEN_RULES:
            context_re = rule.get("context")
            if context_re is not None and not context_re.search(text):
                continue
            reported = False
            for m in rule["pattern"].finditer(text):
                spans = claimed_spans.get(line_no, [])
                if _overlaps(spans, m.span()):
                    continue  # same characters already reported by an earlier rule
                secret = m.group("pw") if rule["id"] == "SECRET011" else m.group(0)
                if _is_allowlisted(secret, var_name):
                    continue
                claimed_spans.setdefault(line_no, []).append(m.span())
                if reported:
                    continue  # claim further duplicates but report each rule once per line
                reported = True
                findings.append(_token_finding(ctx, rule, line_no, text, m, var_name))
    return findings


# ---------------------------------------------------------------------------
# SECRET012: sensitive variable assigned a hardcoded literal
# ---------------------------------------------------------------------------


def _target_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):  # self.password = "..."
        return node.attr
    return ""


def _iter_python_assignments(tree: ast.AST):
    """Yield ``(stmt_node, name, value_node)`` for every name<-value binding.

    Covers plain and annotated assignments (including ``self.attr``), keyword
    arguments (``connect(password="...")``) and literal dict entries
    (``{"api_key": "..."}``).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                name = _target_name(target)
                if name:
                    yield node, name, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            name = _target_name(node.target)
            if name:
                yield node, name, node.value
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg:
                    yield node, kw.arg, kw.value
        elif isinstance(node, ast.Dict):
            for key, val in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    yield node, key.value, val


def _assignment_finding(ctx: FileCtx, line_no: int, name: str, literal: str, *, precision: str, confidence: str,
                        engine: str) -> dict:
    raw_line = ctx.line_text(line_no).strip()
    if literal and literal in raw_line:
        evidence = raw_line.replace(literal, _redact_token(literal))
    else:  # multiline literal or reconstructed gap: synthesize the shape
        evidence = f'{name} = "{_redact_token(literal)}"'
    return make_finding(
        rule_id="SECRET012",
        category=CATEGORY,
        severity="high",
        file=ctx.path,
        line=line_no,
        title="Sensitive variable assigned hardcoded literal",
        evidence=f"{evidence} [{engine}]",
        recommendation="Move the value out of source control: read it from the environment or a secret "
        "manager and rotate the exposed value.",
        confidence=confidence,
        precision=precision,
        fix_snippet={
            "before": evidence,
            "after": _fix_after(ctx, name, "SECRET_VALUE")
        },
    )


def _scan_assignments_ast(ctx: FileCtx, tree: ast.AST, claimed_lines: set) -> list[dict]:
    findings: list[dict] = []
    for node, name, value in _iter_python_assignments(tree):
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue  # os.environ[...], f-strings, tuples, numbers: not hardcoded strings
        if not _SENSITIVE_NAME_RE.search(name):
            continue
        line_no = value.lineno if ctx.is_changed_line(value.lineno) else node.lineno
        if not ctx.is_changed_line(line_no) or line_no in claimed_lines:
            continue
        literal = value.value
        if not _looks_like_secret_value(literal) or _is_allowlisted(literal, name):
            continue
        claimed_lines.add(line_no)
        findings.append(
            _assignment_finding(ctx, line_no, name, literal, precision="high", confidence="high", engine="ast"))
    return findings


def _scan_assignments_regex(ctx: FileCtx, claimed_lines: set, *, precision: str, confidence: str) -> list[dict]:
    findings: list[dict] = []
    for line_no in sorted(ctx.candidate_lines):
        if line_no in claimed_lines:
            continue
        m = _KV_RE.match(ctx.line_text(line_no))
        if not m:
            continue  # comments and free text never look like `name[:=] value`
        name = m.group("name")
        value = _unquote(m.group("value"))
        if not _SENSITIVE_NAME_RE.search(name):
            continue
        if not _looks_like_secret_value(value) or _is_allowlisted(value, name):
            continue
        claimed_lines.add(line_no)
        findings.append(
            _assignment_finding(ctx, line_no, name, value, precision=precision, confidence=confidence, engine="regex"))
    return findings


def _scan_sensitive_assignments(ctx: FileCtx, claimed_lines: set) -> list[dict]:
    """SECRET012 dispatcher: AST for parsable python, key/value regex otherwise."""
    if ctx.language == "python":
        tree, _err = ctx.parse_ast()
        if tree is not None:
            return _scan_assignments_ast(ctx, tree, claimed_lines)
        # diff-only gap reconstruction or syntax error: degrade honestly
        return _scan_assignments_regex(ctx, claimed_lines, precision="low", confidence="low")
    # config formats: the whole statement is one line, the KV match is structural
    return _scan_assignments_regex(ctx, claimed_lines, precision="high", confidence="medium")


# ---------------------------------------------------------------------------
# SECRET013: high-entropy fallback net
# ---------------------------------------------------------------------------

_ENTROPY_CONTEXT_RE = re.compile(r"(?i)key|secret|token|credential")
_ENTROPY_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_\-]{20,}")
_ENTROPY_THRESHOLD = 4.5  # bits/char; mathematically needs >=23 mostly-distinct chars


def _scan_high_entropy(ctx: FileCtx, claimed_lines: set) -> list[dict]:
    findings: list[dict] = []
    for line_no in sorted(ctx.candidate_lines):
        if line_no in claimed_lines:
            continue  # never double-report a line another rule already explained
        text = ctx.line_text(line_no)
        if not _ENTROPY_CONTEXT_RE.search(text):
            continue
        var_name = _extract_var_name(text)
        for m in _ENTROPY_TOKEN_RE.finditer(text):
            token = m.group(0)
            if _is_allowlisted(token, var_name):
                continue
            entropy = _shannon_entropy(token)
            if entropy <= _ENTROPY_THRESHOLD:
                continue
            claimed_lines.add(line_no)
            redacted = text.strip().replace(token, _redact_token(token))
            findings.append(
                make_finding(
                    rule_id="SECRET013",
                    category=CATEGORY,
                    severity="medium",
                    file=ctx.path,
                    line=line_no,
                    title="High-entropy string near credential context",
                    evidence=f"{redacted} (entropy {entropy:.2f} bits/char over {len(token)} chars)",
                    recommendation="Verify whether this random-looking value is a credential; if so rotate "
                    "it and load it from the environment or a secret manager.",
                    confidence="low",
                    precision="low",
                    # no fix_snippet: too uncertain to auto-suggest a rewrite
                ))
            break  # one entropy finding per line is enough signal
    return findings


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run(files: list[FileCtx], mode: str, context: dict) -> list[dict]:  # noqa: ARG001 (contract signature)
    """Entry point, see the module docstring for the rule set.

    ``mode`` needs no special handling here: line scanning is identical in
    repo and diff-only mode, and the AST -> regex degradation for SECRET012
    happens automatically whenever ``parse_ast`` fails.
    """
    findings: list[dict] = []
    for ctx in files or []:
        if ctx.change_type in ("deleted", "binary"):
            continue  # nothing addable to report; deleted secrets are gone
        if ctx.content is None or not ctx.candidate_lines:
            continue  # pure renames / metadata-only changes
        claimed_spans: dict = {}
        findings.extend(_scan_token_rules(ctx, claimed_spans))
        claimed_lines = {line for line, spans in claimed_spans.items() if spans}
        findings.extend(_scan_sensitive_assignments(ctx, claimed_lines))
        findings.extend(_scan_high_entropy(ctx, claimed_lines))
    return findings
