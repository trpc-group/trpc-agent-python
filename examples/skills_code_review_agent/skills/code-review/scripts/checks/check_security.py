# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""security: injection and dangerous-API findings on changed Python lines.

AST-first: every rule is confirmed on the parsed post-image and reported with
``precision="high"``.  When the AST is unavailable (diff-only gap
reconstruction with ``content_complete=False``, or a syntax error) the module
degrades to a per-changed-line regex pass with ``precision="low"`` /
``confidence="low"`` and never raises.

Rules (severity / precision)
----------------------------
SEC001  SQL built dynamically (f-string, ``+``, ``%``, ``.format``) passed as
        the first argument of ``*.execute/executemany/executescript``.  A bare
        name argument is traced one binding step up inside the same scope
        (``query = f"..."`` then ``cur.execute(query)``)   critical / high
SEC002  ``eval``/``exec`` on a non-literal argument         critical / high
        (a constant-literal argument is downgraded to medium)
SEC003  ``subprocess.run/call/check_call/check_output/Popen`` with
        ``shell=True``: interpolated command -> critical, literal command ->
        medium                                     critical|medium / high
SEC004  ``os.system`` / ``os.popen``: graded like SEC003
                                                   critical|medium / high
SEC005  ``yaml.load`` without a Loader, or with Loader/UnsafeLoader
                                                            high / high
SEC006  ``pickle.load(s)`` / ``marshal.load(s)`` / ``shelve.open`` on a
        changed line; confidence=medium because the payload may well be
        trusted local data -- see the note at the rule       high / high
SEC007  ``requests``/``httpx`` call with ``verify=False``    high / high
SEC008  ``tempfile.mktemp`` (filename race)                 medium / high

Patterns deliberately NOT reported (false-positive guards)
----------------------------------------------------------
* SEC001: a first argument that is a plain string literal never fires --
  parameterized queries (``execute("... WHERE id = ?", (uid,))``) and static
  literal SQL are both safe.  A name whose nearest plain re-assignment is a
  static literal is clean (the one-step trace stops at full rebinds:
  ``q = "UPDATE t SET x = ?"; cur.execute(q, (v,))`` stays silent).  Queries
  built by helper calls (``execute(build_query(x))``) are opaque and skipped.
* SEC002: ``ast.literal_eval`` and attribute calls such as ``model.eval()``
  (torch) or ``df.eval(...)`` (pandas) never match -- only the bare builtin
  (or ``builtins.eval/exec``) is flagged.
* SEC003: subprocess calls without ``shell=True`` (argv lists) are safe by
  construction and stay silent; ``shell=flag`` with a non-literal value is
  not provably True and stays silent too.
* SEC005: ``yaml.safe_load`` and ``Loader=SafeLoader/BaseLoader/FullLoader``
  (plus their C variants) are not reported; unknown custom loader classes get
  the benefit of the doubt.
* SEC006: ``json.loads`` and other text codecs are out of scope; unchanged
  pre-existing pickle usage in context lines is never re-reported.
* SEC007: ``verify=False`` on a receiver that cannot be resolved to
  requests/httpx (module attribute, ``from`` import, or a one-step
  ``s = requests.Session()`` / ``httpx.Client()`` binding in the same scope)
  is skipped: a ``verify`` keyword on an unrelated API is not a TLS bug.
* all rules: findings anchor only on changed (candidate) lines -- an
  untouched dangerous call that merely appears in diff context is skipped.
"""

from __future__ import annotations

import ast
import re
import shlex
from typing import Optional

from checks.common import FileCtx, call_name, has_interpolation, has_string_side, make_finding

CATEGORY = "security"

_EXECUTE_NAMES = frozenset({"execute", "executemany", "executescript"})
_EVAL_NAMES = frozenset({"eval", "exec", "builtins.eval", "builtins.exec"})
_SUBPROCESS_NAMES = frozenset({
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
})
_OS_SHELL_NAMES = frozenset({"os.system", "os.popen"})
_DESERIALIZE_NAMES = frozenset({"pickle.load", "pickle.loads", "marshal.load", "marshal.loads", "shelve.open"})
_HTTP_MODULES = frozenset({"requests", "httpx"})
_HTTP_CLIENT_CTORS = frozenset({"requests.Session", "requests.session", "httpx.Client", "httpx.AsyncClient"})
_YAML_SAFE_LOADERS = frozenset({"SafeLoader", "CSafeLoader", "BaseLoader", "CBaseLoader", "FullLoader", "CFullLoader"})
_YAML_UNSAFE_LOADERS = frozenset({"Loader", "UnsafeLoader", "CLoader", "CUnsafeLoader"})

#: nested scopes are analysed separately; never descend into them from outside
_SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

_SQL_KEYWORD_RE = re.compile(
    r"(?i)\b(select|insert|update|delete|drop|create|alter|replace|truncate|merge|grant|revoke|pragma|"
    r"from|where|values|into|table)\b")

_REC_SQL = ("Use a parameterized query: keep the SQL text static with placeholders and pass the values "
            "as the second argument (sqlite3 uses '?', most other DB-API drivers use '%s'). Never "
            "interpolate request data into SQL text.")
_REC_EVAL = ("Avoid eval/exec on dynamic data: use ast.literal_eval for Python literals, json for data, "
             "or an explicit dispatch table for behaviour selection.")
_REC_EVAL_CONST = ("Inline the expression instead of eval/exec on a constant: it hides code from linters, "
                   "type checkers and grep for no benefit.")
_REC_SHELL = ("Pass the command as an argument list and drop shell=True so arguments are never re-parsed "
              "by a shell; use shlex.split for existing command strings and shlex.quote if a shell is "
              "truly required.")
_REC_OS_SHELL = ("Replace os.system/os.popen with subprocess.run([...]) without a shell. If shell syntax "
                 "is really needed, quote every interpolated variable with shlex.quote.")
_REC_YAML = ("Use yaml.safe_load(...) (or Loader=yaml.SafeLoader): yaml.load with the default or unsafe "
             "loader can instantiate arbitrary Python objects from the document.")
_REC_DESERIALIZE = ("Deserializing pickle/marshal/shelve data executes arbitrary code on load. Confirm the "
                    "payload can never come from an untrusted source, or switch to a data-only format such "
                    "as JSON and sign payloads that cross a trust boundary.")
_REC_VERIFY = ("Re-enable certificate verification (verify=True, the default). If an internal CA is the "
               "blocker, pass its bundle via verify='/path/to/ca.pem' instead of disabling TLS.")
_REC_MKTEMP = ("tempfile.mktemp only returns a name: another process can create that file first (symlink "
               "attack). Use tempfile.mkstemp() or tempfile.NamedTemporaryFile, which create the file "
               "atomically.")

# ---------------------------------------------------------------------------
# generic AST helpers
# ---------------------------------------------------------------------------


def _unparse(node: Optional[ast.AST]) -> str:
    """ast.unparse that never raises; empty string on failure."""
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pylint: disable=broad-except
        return ""


def _src_line(ctx: FileCtx, lineno: int, node: Optional[ast.AST] = None) -> str:
    """Real source text of a line; falls back to unparse for blank gap lines."""
    text = ctx.line_text(lineno).strip()
    if not text and node is not None:
        text = _unparse(node)
    return text or f"<line {lineno}>"


def _anchor_line(ctx: FileCtx, node: ast.AST, *extra: int) -> Optional[int]:
    """First changed line covering the node (its span, then the extras).

    Findings may only anchor on candidate lines; ``None`` means the dangerous
    call sits entirely in unchanged context and must not be reported.
    """
    candidates: list[int] = []
    lineno = getattr(node, "lineno", 0) or 0
    if lineno:
        end = getattr(node, "end_lineno", None) or lineno
        candidates.extend(range(lineno, min(end, lineno + 20) + 1))
    candidates.extend(line for line in extra if line)
    for line in candidates:
        if ctx.is_changed_line(line):
            return line
    return None


def _iter_scopes(tree: ast.AST):
    """Yield the statement list of every scope: module, functions, class bodies."""
    yield list(getattr(tree, "body", []))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield list(node.body)


def _iter_scope_nodes(stmts: list):
    """Every AST node under these statements without entering nested scopes."""
    stack = list(stmts)
    while stack:
        node = stack.pop()
        if isinstance(node, _SCOPE_TYPES):
            continue  # analysed as its own scope by _iter_scopes
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _collect_bindings(stmts: list) -> dict:
    """name -> sorted [(lineno, rhs, is_augmented_add)] for this scope only."""
    bindings: dict[str, list[tuple[int, ast.AST, bool]]] = {}
    for node in _iter_scope_nodes(stmts):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            bindings.setdefault(node.targets[0].id, []).append((node.lineno, node.value, False))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            bindings.setdefault(node.target.id, []).append((node.lineno, node.value, False))
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and isinstance(node.op, ast.Add):
            bindings.setdefault(node.target.id, []).append((node.lineno, node.value, True))
    for items in bindings.values():
        items.sort(key=lambda item: item[0])
    return bindings


def _collect_import_aliases(tree: ast.AST) -> tuple[dict, dict]:
    """(module alias map, from-import map) for canonical call-name resolution."""
    mod_alias: dict[str, str] = {}
    func_alias: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    mod_alias[alias.asname] = alias.name
                else:  # "import os.path" binds the top-level name "os"
                    top = alias.name.split(".")[0]
                    mod_alias[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                if alias.name != "*":
                    func_alias[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return mod_alias, func_alias


def _canonical_name(dotted: str, mod_alias: dict, func_alias: dict) -> str:
    """Resolve aliases: ``sp.run`` -> ``subprocess.run``, bare ``load`` -> ``yaml.load``."""
    if not dotted:
        return ""
    head, sep, rest = dotted.partition(".")
    if sep:
        target = mod_alias.get(head)
        return f"{target}.{rest}" if target else dotted
    return func_alias.get(dotted, dotted)


def _is_str_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _literal_fragments(expr: ast.AST) -> list[str]:
    """All constant string fragments anywhere inside the expression."""
    return [n.value for n in ast.walk(expr) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _looks_like_sql(expr: ast.AST) -> bool:
    """True when any literal fragment of the expression contains a SQL keyword."""
    return any(_SQL_KEYWORD_RE.search(text) for text in _literal_fragments(expr))


def _trace_dynamic_binding(bindings: dict, name: str, call_lineno: int):
    """One-step taint trace for ``name`` used at ``call_lineno`` in this scope.

    Walks bindings backwards from the call:
    * an interpolated RHS (f-string / ``+`` / ``%`` / ``.format``) => dynamic
      (kind ``"interp"``);
    * ``name += <non-constant>`` while some binding shows the name holds a
      string => dynamic build by concatenation (kind ``"concat"``);
    * a plain re-assignment to a static value is a full rebind: the trace
      stops there and the name is considered clean (explicit negative:
      ``q = "UPDATE t SET x = ?"`` then ``execute(q, (v,))`` is never
      reported);
    * ``+=`` of a pure string constant keeps walking up (still static).
    Returns ``(lineno, rhs, kind)`` or ``None``.
    """
    items = [item for item in bindings.get(name, []) if item[0] < call_lineno]
    if not items:
        return None  # parameter or outer-scope variable: unknown, stay silent
    saw_string = any(has_string_side(rhs) for _line, rhs, _aug in items)
    for lineno, rhs, is_aug in reversed(items):
        if has_interpolation(rhs):
            return lineno, rhs, "interp"
        if is_aug:
            if saw_string and not _is_str_constant(rhs):
                return lineno, rhs, "concat"
            continue  # static append: keep looking further up
        return None  # full rebind to a non-dynamic value: clean
    return None


def _two_line_evidence(ctx: FileCtx, bind_line: int, call: ast.Call) -> str:
    return (f"L{bind_line}: {_src_line(ctx, bind_line)}  ->  "
            f"L{call.lineno}: {_src_line(ctx, call.lineno, call)}")


# ---------------------------------------------------------------------------
# SEC001: SQL injection
# ---------------------------------------------------------------------------


def _parameterize(expr: ast.AST):
    """Best-effort ``(sql_text, param_sources)`` for a dynamic query, else None."""
    if isinstance(expr, ast.JoinedStr):
        sql, params = [], []
        for value in expr.values:
            if isinstance(value, ast.Constant):
                sql.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                sql.append("%s")
                params.append(_unparse(value.value))
        return "".join(sql), params
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Mod) and _is_str_constant(expr.left):
        right = expr.right
        params = [_unparse(elt) for elt in right.elts] if isinstance(right, ast.Tuple) else [_unparse(right)]
        return expr.left.value, params
    if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "format"
            and _is_str_constant(expr.func.value)):
        sql = re.sub(r"\{[^{}]*\}", "%s", expr.func.value.value)
        params = [_unparse(arg) for arg in expr.args]
        params += [_unparse(kw.value) for kw in expr.keywords if kw.arg]
        return sql, params
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        sql, params = [], []

        def _flatten(node: ast.AST) -> None:
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                _flatten(node.left)
                _flatten(node.right)
            elif _is_str_constant(node):
                sql.append(node.value)
            else:
                sql.append("%s")
                params.append(_unparse(node))

        _flatten(expr)
        return "".join(sql), params
    return None


def _sql_fix(ctx: FileCtx, call: ast.Call, dyn_expr: ast.AST, var_name: Optional[str],
             bind_line: Optional[int]) -> Optional[dict]:
    """Parameterized rewrite filled with the real matched code, or None."""
    parts = _parameterize(dyn_expr)
    if parts is None or not parts[1] or not all(parts[1]):
        return None
    sql, params = parts
    sql = re.sub(r"%[dirf]", "%s", sql)  # DB-API drivers only accept %s markers
    sql = re.sub(r"'(%s)'", r"\1", sql).replace('"%s"', "%s")  # drop quotes around markers
    func_src = _unparse(call.func) or "cursor.execute"
    args_src = f"({params[0]},)" if len(params) == 1 else "(" + ", ".join(params) + ")"
    if var_name is None or bind_line is None:
        return {"before": _src_line(ctx, call.lineno, call), "after": f"{func_src}({sql!r}, {args_src})"}
    before = f"{_src_line(ctx, bind_line)}\n{_src_line(ctx, call.lineno, call)}"
    after = f"{var_name} = {sql!r}\n{func_src}({var_name}, {args_src})"
    return {"before": before, "after": after}


def _check_sql(ctx: FileCtx, call: ast.Call, canonical: str, bindings: dict, findings: list) -> None:
    last = canonical.rsplit(".", 1)[-1]
    if last not in _EXECUTE_NAMES or not call.args:
        return
    arg0 = call.args[0]
    if isinstance(arg0, ast.Constant):
        # Explicit negative: a literal first argument is not injectable, with
        # or without a parameter tuple/list/dict as the second argument
        # (cursor.execute("SELECT ... WHERE id = ?", (uid,)) stays silent).
        return
    if has_interpolation(arg0):
        anchor = _anchor_line(ctx, call)
        if anchor is None:
            return
        findings.append(
            make_finding(
                rule_id="SEC001",
                category=CATEGORY,
                severity="critical",
                file=ctx.path,
                line=anchor,
                title=f"SQL built with string interpolation passed to {last}()",
                evidence=_src_line(ctx, call.lineno, call),
                recommendation=_REC_SQL,
                confidence="high" if _looks_like_sql(arg0) else "medium",
                precision="high",
                fix_snippet=_sql_fix(ctx, call, arg0, None, None),
            ))
        return
    if not isinstance(arg0, ast.Name):
        return  # helper-built / opaque expression: cannot confirm, stay silent
    traced = _trace_dynamic_binding(bindings, arg0.id, call.lineno)
    if traced is None:
        return
    bind_line, rhs, kind = traced
    anchor = _anchor_line(ctx, call, bind_line)
    if anchor is None:
        return
    confidence = ("high" if _looks_like_sql(rhs) else "medium") if kind == "interp" else "medium"
    findings.append(
        make_finding(
            rule_id="SEC001",
            category=CATEGORY,
            severity="critical",
            file=ctx.path,
            line=anchor,
            title=f"Dynamically built SQL variable '{arg0.id}' passed to {last}()",
            evidence=_two_line_evidence(ctx, bind_line, call),
            recommendation=_REC_SQL,
            confidence=confidence,
            precision="high",
            fix_snippet=_sql_fix(ctx, call, rhs, arg0.id, bind_line),
        ))


# ---------------------------------------------------------------------------
# SEC002: eval / exec
# ---------------------------------------------------------------------------


def _check_eval(ctx: FileCtx, call: ast.Call, canonical: str, findings: list) -> None:
    if canonical not in _EVAL_NAMES or not call.args:
        return
    # ast.literal_eval and attribute calls (model.eval(), df.eval(...)) never
    # reach this point: only the bare/builtins name matches _EVAL_NAMES.
    anchor = _anchor_line(ctx, call)
    if anchor is None:
        return
    name = canonical.rsplit(".", 1)[-1]
    arg0 = call.args[0]
    evidence = _src_line(ctx, call.lineno, call)
    if isinstance(arg0, ast.Constant):
        fix = None
        if name == "eval" and isinstance(arg0.value, str):
            fix = {"before": evidence, "after": f"{arg0.value}  # inline the expression instead of eval()"}
        findings.append(
            make_finding(
                rule_id="SEC002",
                category=CATEGORY,
                severity="medium",
                file=ctx.path,
                line=anchor,
                title=f"{name}() on a constant literal",
                evidence=evidence,
                recommendation=_REC_EVAL_CONST,
                confidence="high",
                precision="high",
                fix_snippet=fix,
            ))
        return
    fix = None
    if name == "eval":
        arg_src = _unparse(arg0)
        if arg_src:
            fix = {"before": evidence, "after": f"ast.literal_eval({arg_src})  # only if the input is a Python literal"}
    findings.append(
        make_finding(
            rule_id="SEC002",
            category=CATEGORY,
            severity="critical",
            file=ctx.path,
            line=anchor,
            title=f"{name}() on a dynamic expression",
            evidence=evidence,
            recommendation=_REC_EVAL,
            confidence="high",
            precision="high",
            fix_snippet=fix,
        ))


# ---------------------------------------------------------------------------
# SEC003 / SEC004: shell execution
# ---------------------------------------------------------------------------


def _grade_command(bindings: dict, cmd: Optional[ast.AST], call_lineno: int):
    """(severity, confidence, traced) for a shell command expression."""
    if cmd is None:
        return "medium", "medium", None
    if has_interpolation(cmd):
        return "critical", "high", None
    if _is_str_constant(cmd):
        return "medium", "high", None
    if isinstance(cmd, ast.Name):
        traced = _trace_dynamic_binding(bindings, cmd.id, call_lineno)
        if traced is not None:
            return "critical", ("high" if traced[2] == "interp" else "medium"), traced
    return "medium", "medium", None  # unknown command source; shell=True itself is certain


def _shell_argv_tokens(cmd: Optional[ast.AST]) -> Optional[list[str]]:
    """Best-effort argv token sources for a literal or f-string command."""
    if _is_str_constant(cmd):
        try:
            return [repr(tok) for tok in shlex.split(cmd.value)] or None
        except ValueError:
            return None
    if not isinstance(cmd, ast.JoinedStr):
        return None
    words: list[list[tuple[str, str]]] = [[]]
    for value in cmd.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            pieces = str(value.value).split(" ")
            for idx, piece in enumerate(pieces):
                if idx:
                    words.append([])
                if piece:
                    words[-1].append(("t", piece))
        elif isinstance(value, ast.FormattedValue):
            if value.format_spec is not None or value.conversion not in (-1, None):
                return None  # conversion/format specs are beyond a safe rewrite
            src = _unparse(value.value)
            if not src:
                return None
            words[-1].append(("e", src))
        else:
            return None
    tokens: list[str] = []
    for word in words:
        if not word:
            continue
        if all(kind == "t" for kind, _text in word):
            tokens.append(repr("".join(text for _kind, text in word)))
        elif len(word) == 1:
            tokens.append(word[0][1])
        else:  # mixed token like --host={h}: keep it as one argv element
            inner = "".join(text if kind == "t" else "{" + text + "}" for kind, text in word)
            tokens.append('f"' + inner + '"')
    return tokens or None


def _check_subprocess(ctx: FileCtx, call: ast.Call, canonical: str, bindings: dict, findings: list) -> None:
    if canonical not in _SUBPROCESS_NAMES:
        return
    shell_kw = next((kw for kw in call.keywords if kw.arg == "shell"), None)
    if shell_kw is None:
        return  # argv-style call without a shell: safe by construction
    if not (isinstance(shell_kw.value, ast.Constant) and shell_kw.value.value is True):
        return  # shell=False or a non-literal flag: not provably unsafe
    cmd = call.args[0] if call.args else next((kw.value for kw in call.keywords if kw.arg == "args"), None)
    severity, confidence, traced = _grade_command(bindings, cmd, call.lineno)
    anchor = _anchor_line(ctx, call, traced[0] if traced else 0)
    if anchor is None:
        return
    if traced is not None:
        evidence = _two_line_evidence(ctx, traced[0], call)
    else:
        evidence = _src_line(ctx, call.lineno, call)
    fix = None
    tokens = _shell_argv_tokens(cmd)
    func_src = _unparse(call.func) or canonical
    keep = [f"{kw.arg}={_unparse(kw.value)}" for kw in call.keywords if kw.arg not in (None, "shell")]
    tail = (", " + ", ".join(keep)) if keep else ""
    if tokens:
        fix = {"before": _src_line(ctx, call.lineno, call), "after": f"{func_src}([{', '.join(tokens)}]{tail})"}
    elif cmd is not None and _unparse(cmd):
        fix = {"before": _src_line(ctx, call.lineno, call), "after": f"{func_src}(shlex.split({_unparse(cmd)}){tail})"}
    findings.append(
        make_finding(
            rule_id="SEC003",
            category=CATEGORY,
            severity=severity,
            file=ctx.path,
            line=anchor,
            title=f"{canonical}() called with shell=True",
            evidence=evidence,
            recommendation=_REC_SHELL,
            confidence=confidence,
            precision="high",
            fix_snippet=fix,
        ))


def _check_os_shell(ctx: FileCtx, call: ast.Call, canonical: str, bindings: dict, findings: list) -> None:
    if canonical not in _OS_SHELL_NAMES:
        return
    cmd = call.args[0] if call.args else None
    severity, confidence, traced = _grade_command(bindings, cmd, call.lineno)
    anchor = _anchor_line(ctx, call, traced[0] if traced else 0)
    if anchor is None:
        return
    if traced is not None:
        evidence = _two_line_evidence(ctx, traced[0], call)
    else:
        evidence = _src_line(ctx, call.lineno, call)
    fix = None
    tokens = _shell_argv_tokens(cmd)
    if tokens:
        argv = ", ".join(tokens)
        if canonical == "os.system":
            after = f"subprocess.run([{argv}], check=True)"
        else:  # os.popen reads the command's stdout
            after = f"subprocess.run([{argv}], capture_output=True, text=True).stdout"
        fix = {"before": _src_line(ctx, call.lineno, call), "after": after}
    findings.append(
        make_finding(
            rule_id="SEC004",
            category=CATEGORY,
            severity=severity,
            file=ctx.path,
            line=anchor,
            title=f"{canonical}() runs a command through the shell",
            evidence=evidence,
            recommendation=_REC_OS_SHELL,
            confidence=confidence,
            precision="high",
            fix_snippet=fix,
        ))


# ---------------------------------------------------------------------------
# SEC005..SEC008: unsafe library usage
# ---------------------------------------------------------------------------


def _check_yaml(ctx: FileCtx, call: ast.Call, canonical: str, findings: list) -> None:
    if canonical != "yaml.load":
        return  # yaml.safe_load has its own name and never matches
    loader = next((kw.value for kw in call.keywords if kw.arg == "Loader"), None)
    if loader is None and len(call.args) > 1:
        loader = call.args[1]  # yaml.load(stream, SomeLoader) positional form
    if loader is not None:
        loader_last = call_name(loader).rsplit(".", 1)[-1]
        if loader_last in _YAML_SAFE_LOADERS:
            return  # explicit negative: SafeLoader/BaseLoader/FullLoader
        if loader_last not in _YAML_UNSAFE_LOADERS:
            return  # unknown custom loader: benefit of the doubt (precision first)
    anchor = _anchor_line(ctx, call)
    if anchor is None:
        return
    evidence = _src_line(ctx, call.lineno, call)
    arg_src = _unparse(call.args[0]) if call.args else "stream"
    findings.append(
        make_finding(
            rule_id="SEC005",
            category=CATEGORY,
            severity="high",
            file=ctx.path,
            line=anchor,
            title="yaml.load without a safe Loader",
            evidence=evidence,
            recommendation=_REC_YAML,
            confidence="high",
            precision="high",
            fix_snippet={
                "before": evidence,
                "after": f"yaml.safe_load({arg_src})"
            },
        ))


def _check_deserialize(ctx: FileCtx, call: ast.Call, canonical: str, findings: list) -> None:
    if canonical not in _DESERIALIZE_NAMES:
        return
    anchor = _anchor_line(ctx, call)
    if anchor is None:
        return
    evidence = _src_line(ctx, call.lineno, call)
    fix = None
    if canonical.startswith(("pickle.", "marshal.")) and call.args:
        arg_src = _unparse(call.args[0])
        if arg_src:
            fix = {"before": evidence, "after": f"json.loads({arg_src})  # if the payload can be data-only"}
    # confidence=medium on purpose: the payload may be trusted local data
    # (e.g. a cache this same program wrote); static analysis cannot see
    # provenance, so this is a "verify the trust boundary" finding, not a
    # certain vulnerability.
    findings.append(
        make_finding(
            rule_id="SEC006",
            category=CATEGORY,
            severity="high",
            file=ctx.path,
            line=anchor,
            title=f"{canonical}() deserializes data that can execute code",
            evidence=evidence,
            recommendation=_REC_DESERIALIZE,
            confidence="medium",
            precision="high",
            fix_snippet=fix,
        ))


def _check_verify(ctx: FileCtx, call: ast.Call, canonical: str, bindings: dict, mod_alias: dict, func_alias: dict,
                  findings: list) -> None:
    verify_kw = next((kw for kw in call.keywords if kw.arg == "verify"), None)
    if verify_kw is None or not (isinstance(verify_kw.value, ast.Constant) and verify_kw.value.value is False):
        return  # verify=True / a CA-bundle path / absent: nothing to report
    resolved = canonical.split(".", 1)[0] in _HTTP_MODULES
    if not resolved and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        # one-step receiver trace: s = requests.Session(); s.get(..., verify=False)
        items = [item for item in bindings.get(call.func.value.id, []) if item[0] < call.lineno]
        if items and isinstance(items[-1][1], ast.Call):
            ctor = _canonical_name(call_name(items[-1][1].func), mod_alias, func_alias)
            resolved = ctor in _HTTP_CLIENT_CTORS
    if not resolved:
        return  # verify=False on an unresolved receiver is not provably a TLS bug
    anchor = _anchor_line(ctx, call)
    if anchor is None:
        return
    before = _src_line(ctx, verify_kw.value.lineno)
    if "verify" not in before:
        before = _unparse(call) or _src_line(ctx, call.lineno, call)
    after = re.sub(r"verify\s*=\s*False", "verify=True", before)
    findings.append(
        make_finding(
            rule_id="SEC007",
            category=CATEGORY,
            severity="high",
            file=ctx.path,
            line=anchor,
            title="TLS certificate verification disabled (verify=False)",
            evidence=_src_line(ctx, call.lineno, call),
            recommendation=_REC_VERIFY,
            confidence="high",
            precision="high",
            fix_snippet={
                "before": before,
                "after": after
            } if after != before else None,
        ))


def _check_mktemp(ctx: FileCtx, call: ast.Call, canonical: str, findings: list) -> None:
    if canonical != "tempfile.mktemp":
        return
    anchor = _anchor_line(ctx, call)
    if anchor is None:
        return
    evidence = _src_line(ctx, call.lineno, call)
    args_src = ", ".join([_unparse(arg)
                          for arg in call.args] + [f"{kw.arg}={_unparse(kw.value)}" for kw in call.keywords if kw.arg])
    findings.append(
        make_finding(
            rule_id="SEC008",
            category=CATEGORY,
            severity="medium",
            file=ctx.path,
            line=anchor,
            title="tempfile.mktemp is vulnerable to a filename race",
            evidence=evidence,
            recommendation=_REC_MKTEMP,
            confidence="high",
            precision="high",
            fix_snippet={
                "before": evidence,
                "after": f"fd, path = tempfile.mkstemp({args_src})  # or NamedTemporaryFile"
            },
        ))


# ---------------------------------------------------------------------------
# AST driver
# ---------------------------------------------------------------------------


def _match_call(ctx: FileCtx, call: ast.Call, bindings: dict, mod_alias: dict, func_alias: dict,
                findings: list) -> None:
    canonical = _canonical_name(call_name(call.func), mod_alias, func_alias)
    if canonical:
        _check_sql(ctx, call, canonical, bindings, findings)
        _check_eval(ctx, call, canonical, findings)
        _check_subprocess(ctx, call, canonical, bindings, findings)
        _check_os_shell(ctx, call, canonical, bindings, findings)
        _check_yaml(ctx, call, canonical, findings)
        _check_deserialize(ctx, call, canonical, findings)
        _check_mktemp(ctx, call, canonical, findings)
    _check_verify(ctx, call, canonical, bindings, mod_alias, func_alias, findings)


def _scan_python_ast(ctx: FileCtx, tree: ast.AST) -> list[dict]:
    findings: list[dict] = []
    mod_alias, func_alias = _collect_import_aliases(tree)
    for scope_stmts in _iter_scopes(tree):
        bindings = _collect_bindings(scope_stmts)
        for node in _iter_scope_nodes(scope_stmts):
            if isinstance(node, ast.Call):
                _match_call(ctx, node, bindings, mod_alias, func_alias, findings)
    return findings


# ---------------------------------------------------------------------------
# regex fallback (AST unavailable): precision=low, confidence=low
# ---------------------------------------------------------------------------

_FB_DYNAMIC_CMD_RE = re.compile(r"""f['"]|%\s*\(|\.format\s*\(|\+""")


def _fb_grade_cmd(text: str) -> str:
    return "critical" if _FB_DYNAMIC_CMD_RE.search(text) else "medium"


def _fb_grade_eval(text: str) -> str:
    return "medium" if re.search(r"""(?:eval|exec)\s*\(\s*['"]""", text) else "critical"


_FALLBACK_RULES = (
    {
        "id": "SEC001",
        "title": "SQL built with an f-string passed to execute()",
        "pattern": re.compile(r"""\.execut(?:e|emany|escript)\s*\(\s*f['"]"""),
        "severity": "critical",
        "recommendation": _REC_SQL,
    },
    {
        "id": "SEC002",
        "title": "eval()/exec() call",
        # lookbehind excludes attribute calls (.eval) and ast.literal_eval
        "pattern": re.compile(r"(?<![\w.])(?:eval|exec)\s*\("),
        "grade": _fb_grade_eval,
        "recommendation": _REC_EVAL,
    },
    {
        "id": "SEC003",
        "title": "subprocess call with shell=True",
        "pattern": re.compile(r"\bsubprocess\.(?:run|call|check_call|check_output|Popen)\s*\(.*shell\s*=\s*True"),
        "grade": _fb_grade_cmd,
        "recommendation": _REC_SHELL,
    },
    {
        "id": "SEC004",
        "title": "os.system/os.popen shell command",
        "pattern": re.compile(r"\bos\.(?:system|popen)\s*\("),
        "grade": _fb_grade_cmd,
        "recommendation": _REC_OS_SHELL,
    },
    {
        "id": "SEC005",
        "title": "yaml.load without a safe Loader",
        "pattern": re.compile(r"\byaml\.load\s*\("),
        "veto": re.compile(r"SafeLoader|BaseLoader|FullLoader|safe_load"),
        "severity": "high",
        "recommendation": _REC_YAML,
    },
    {
        "id": "SEC006",
        "title": "pickle/marshal/shelve deserialization",
        "pattern": re.compile(r"\b(?:pickle\.loads?|marshal\.loads?|shelve\.open)\s*\("),
        "severity": "high",
        "recommendation": _REC_DESERIALIZE,
    },
    {
        "id": "SEC007",
        "title": "TLS certificate verification disabled (verify=False)",
        "pattern": re.compile(r"\bverify\s*=\s*False\b"),
        "require": re.compile(r"\brequests\.|\bhttpx\."),
        "severity": "high",
        "recommendation": _REC_VERIFY,
    },
    {
        "id": "SEC008",
        "title": "tempfile.mktemp is vulnerable to a filename race",
        "pattern": re.compile(r"\btempfile\.mktemp\s*\(|(?<![\w.])mktemp\s*\("),
        "severity": "medium",
        "recommendation": _REC_MKTEMP,
    },
)


def _scan_regex_fallback(ctx: FileCtx) -> list[dict]:
    """Per-changed-line degradation when the post-image does not parse."""
    findings: list[dict] = []
    for line_no in sorted(ctx.candidate_lines):
        text = ctx.line_text(line_no)
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            continue  # blank gap lines and comments never carry live calls
        for rule in _FALLBACK_RULES:
            if not rule["pattern"].search(text):
                continue
            require = rule.get("require")
            if require is not None and not require.search(text):
                continue
            veto = rule.get("veto")
            if veto is not None and veto.search(text):
                continue
            grade = rule.get("grade")
            findings.append(
                make_finding(
                    rule_id=rule["id"],
                    category=CATEGORY,
                    severity=grade(text) if grade else rule["severity"],
                    file=ctx.path,
                    line=line_no,
                    title=rule["title"] + " (regex fallback)",
                    evidence=stripped,
                    recommendation=rule["recommendation"],
                    confidence="low",
                    precision="low",
                ))
            break  # one security finding per line is enough in fallback mode
    return findings


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run(files: list[FileCtx], mode: str, context: dict) -> list[dict]:  # noqa: ARG001 (contract signature)
    """Entry point, see the module docstring for the rule set.

    ``mode`` needs no branching: repo and diff-only files go through the same
    AST pass, and the regex degradation kicks in automatically whenever
    ``parse_ast`` fails (typically ``content_complete=False`` reconstructions).
    """
    findings: list[dict] = []
    for ctx in files or []:
        if ctx.change_type in ("deleted", "binary"):
            continue  # nothing executable is being added
        if ctx.language != "python" or ctx.content is None or not ctx.candidate_lines:
            continue  # every rule targets Python APIs on changed lines
        try:
            tree, _err = ctx.parse_ast()
            if tree is not None:
                findings.extend(_scan_python_ast(ctx, tree))
            else:
                findings.extend(_scan_regex_fallback(ctx))
        except Exception:  # pylint: disable=broad-except
            # Contract: a check must never raise.  Degrade to the regex pass;
            # _scan_python_ast builds its list before extend, so no partial
            # duplicates can leak through.
            try:
                findings.extend(_scan_regex_fallback(ctx))
            except Exception:  # pragma: no cover - defensive double fallback
                continue
    return findings
