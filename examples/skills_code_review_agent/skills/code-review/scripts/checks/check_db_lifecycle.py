# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""db_lifecycle: database connection / cursor / transaction lifecycle checks.

AST-first, function-scope analysis of the post-image.  Module-level
connections are deliberately ignored: a global connection is usually a
long-lived singleton and flagging it would be noise.  The category boundary
with ``resource_leak`` is by object type, not by mechanism: database objects
(connections, cursors, transactions) are reported here and only here; files,
sockets, locks, subprocesses and pools belong to resource_leak.  Both checks
share the same ownership-transfer philosophy but are implemented
independently on purpose — check modules never import each other.

Rules (severity/precision/confidence)
-------------------------------------
DB001  connection from ``<driver>.connect(...)`` (sqlite3, psycopg2, pymysql,
       MySQLdb, mysql.connector, cx_Oracle, pyodbc — import-alias aware, plus
       bare ``connect(...)`` when one of those modules is imported and the
       name is not locally rebound) assigned to a local variable with no
       ``var.close()``, no ``with var``, and no ownership transfer in the
       same function.                                          high/high/high
DB002  ``var.cursor()`` result never closed / with-managed / transferred.
       Most DB-API drivers reclaim cursors on GC, so the harm is exhausted
       server-side handles under load, not a hard leak ->   medium/high/medium
DB003  a string-literal INSERT/UPDATE/DELETE/REPLACE/CREATE/DROP/ALTER is
       executed while the function shows no ``.commit()``/``.rollback()``
       (method or bare call), no ``execute("COMMIT"/"ROLLBACK"/"END")``, no
       transaction-managing ``with`` (``with conn:``, ``with <driver>
       .connect(...)``, ``with x.begin():``) and the word ``autocommit``
       nowhere in the function source.  SELECT never fires.  Autocommit set
       outside the function is invisible, hence precision=low. high/low/medium
DB004  ``x.begin()`` outside a ``with`` header, or ``execute("BEGIN...")``,
       with no commit/rollback evidence in the function.  Subsumes DB003 in
       the same function: one transaction complaint, not two.  high/high/high
DB005  DB001 variant, mutually exclusive with it (fires only when a close
       exists): ``var.close()`` is not inside any ``finally`` block while at
       least one other call — a statement that can raise — sits strictly
       between connect and the first close, so the connection leaks on the
       exception path.  Any intervening Call counts (even logging), hence
       precision=low.                                       medium/low/medium

Ownership transfer — deliberately NOT reported (false-positive guards)
----------------------------------------------------------------------
A tracked connection/cursor variable counts as handed off, and stays silent,
when in the same function it is:

* returned or yielded *directly* (``return conn``, ``return conn, cur``,
  ``yield conn`` — but NOT ``return cur.rowcount``: attribute or method
  results do not carry the resource itself);
* stored or aliased by an assignment whose value exposes the variable
  directly (``self.conn = conn``, ``pool[key] = conn``, ``conns = [conn]``,
  ``other = conn``);
* passed as an argument to any call (``register(conn)``,
  ``contextlib.closing(conn)``, ``atexit.register(conn.close)``, even
  ``log.info("%s", conn)`` — recall is traded for precision).  Receiver
  position (``conn.cursor()``, ``conn.execute(...)``) is not an argument and
  does not transfer;
* used as a context manager (``with conn:``): for sqlite3/psycopg2 this
  commits rather than closes, but flagging the idiomatic form would be noise;
* declared ``global`` (module lifetime, somebody else's problem).

Also never reported: module/class-level connections (no enclosing function);
``with <driver>.connect(...) as conn:`` (never tracked — not an Assign);
``with contextlib.closing(x.cursor()) as cur:`` (cursor is a call argument);
close/transfer evidence found inside *nested* defs still suppresses (a
cleanup closure counts as handling); and anything anchored on an unchanged
line — every finding anchors on the resource-opening (or begin/execute) line
and that line must be part of the diff (``ctx.is_changed_line``).

Diff-only robustness
--------------------
When ``parse_ast()`` fails (gap-reconstructed partial content or a syntax
error) this module never raises: it degrades to a conservative per-changed-
line regex that reports only DB001-style ``var = <driver>.connect(...)``
lines with no visible ``var.close()`` / ``with var`` / ``return var`` /
``yield var`` anywhere in the visible text (precision=low, confidence=low).
DB002-DB005 need real scope analysis and are skipped in the fallback.
"""

from __future__ import annotations

import ast
import re

from checks.common import FileCtx, call_name, make_finding

CATEGORY = "db_lifecycle"

#: driver modules whose ``connect()`` yields a DB-API connection object
_DB_MODULES = frozenset({
    "sqlite3",
    "psycopg2",
    "pymysql",
    "MySQLdb",
    "mysql.connector",
    "cx_Oracle",
    "pyodbc",
})
_WRITE_SQL_RE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\b", re.IGNORECASE)
_BEGIN_SQL_RE = re.compile(r"^\s*BEGIN\b", re.IGNORECASE)
_TXN_END_SQL_RE = re.compile(r"^\s*(COMMIT|ROLLBACK|END)\b", re.IGNORECASE)
_AUTOCOMMIT_RE = re.compile(r"autocommit", re.IGNORECASE)
#: diff-only fallback: ``var = <driver>.connect(...)`` on one changed line
_CONNECT_ASSIGN_RE = re.compile(r"^\s*(?P<var>[A-Za-z_]\w*)\s*(?::[^=]+)?=\s*"
                                r"(?:sqlite3|psycopg2|pymysql|MySQLdb|mysql\.connector|cx_Oracle|pyodbc)"
                                r"\.connect\s*\(")

_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)
#: ast.TryStar only exists on 3.11+; tolerate both
_TRY_TYPES = tuple(t for t in (getattr(ast, "Try", None), getattr(ast, "TryStar", None)) if t is not None)


class _Imports:
    """Import bindings needed to resolve DB ``connect`` calls."""

    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}  # local alias -> real dotted module
        self.connect_names: set[str] = set()  # names bound to a DB module's connect()
        self.any_db = False  # at least one driver module is imported
        self.shadowed: set[str] = set()  # local rebindings of the name "connect"

    def resolve(self, dotted: str) -> str:
        """Map an aliased module reference back to its real dotted path."""
        if dotted in self.aliases:
            return self.aliases[dotted]
        head, sep, tail = dotted.partition(".")
        if head in self.aliases:
            return self.aliases[head] + sep + tail
        return dotted


def _collect_imports(tree: ast.AST) -> _Imports:
    """One pass over the module for driver imports and 'connect' shadowing."""
    info = _Imports()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _DB_MODULES:
                    info.any_db = True
                if alias.asname:
                    info.aliases[alias.asname] = alias.name
                    if alias.asname == "connect":
                        info.shadowed.add("connect")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            is_db = node.level == 0 and module in _DB_MODULES
            if is_db:
                info.any_db = True
            for alias in node.names:
                bound = alias.asname or alias.name
                if is_db and alias.name == "connect":
                    info.connect_names.add(bound)
                elif bound == "connect":
                    info.shadowed.add("connect")  # connect imported from a non-DB module
        elif isinstance(node, _FUNC_TYPES + (ast.ClassDef, )):
            if node.name == "connect":
                info.shadowed.add("connect")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name) and sub.id == "connect":
                        info.shadowed.add("connect")
    return info


def _connect_call_kind(call: ast.Call, imports: _Imports) -> str:
    """Dotted source name when the call opens a DB connection, else ""."""
    name = call_name(call.func)
    if not name:
        return ""
    if name in imports.connect_names:  # from psycopg2 import connect [as pg_connect]
        return name
    if name == "connect":
        # Bare connect() counts only while a driver module is imported and the
        # name is not locally rebound (def connect / connect = ... / foreign import).
        if imports.any_db and "connect" not in imports.shadowed:
            return name
        return ""
    if name.endswith(".connect"):
        base = imports.resolve(name[:-len(".connect")])
        if base in _DB_MODULES:
            return name
    return ""


def _sql_literal(call: ast.Call) -> str:
    """First positional argument as a string literal head, "" when dynamic.

    Plain string constants and the leading constant chunk of an f-string both
    count: an interpolated tail does not change the statement verb.
    """
    if not call.args:
        return ""
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr) and arg.values:
        first = arg.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return ""


def _direct_names(node: ast.AST) -> set[str]:
    """Names exposed *directly* by an expression (ownership-carrying positions).

    ``conn`` and containers of it (tuple/list/set/dict values, starred,
    if-expressions) qualify; ``conn.attr`` / ``conn.method()`` do not — an
    attribute result does not carry the resource itself.
    """
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out: set[str] = set()
        for elt in node.elts:
            out |= _direct_names(elt)
        return out
    if isinstance(node, ast.Dict):
        out = set()
        for value in node.values:
            if value is not None:
                out |= _direct_names(value)
        return out
    if isinstance(node, ast.Starred):
        return _direct_names(node.value)
    if isinstance(node, ast.IfExp):
        return _direct_names(node.body) | _direct_names(node.orelse)
    if isinstance(node, ast.NamedExpr):
        return _direct_names(node.value)
    if isinstance(node, ast.Await):
        return _direct_names(node.value)
    return set()


def _flagged_walk(root: ast.AST) -> list[tuple[ast.AST, bool]]:
    """Whole-subtree (node, inside_finally) pairs.

    Nested def/class bodies ARE included on purpose: close/transfer evidence
    found anywhere in the subtree (e.g. a cleanup closure calling
    ``conn.close()``) suppresses findings — the FP-avoiding direction.
    """
    out: list[tuple[ast.AST, bool]] = []

    def visit(node: ast.AST, fin: bool) -> None:
        out.append((node, fin))
        if isinstance(node, _TRY_TYPES):
            for stmt in list(node.body) + list(node.orelse):
                visit(stmt, fin)
            for handler in node.handlers:
                visit(handler, fin)
            for stmt in node.finalbody:
                visit(stmt, True)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, fin)

    visit(root, False)
    return out


def _scope_nodes(func: ast.AST) -> list[ast.AST]:
    """Nodes of the function's own scope: nested def/class/lambda excluded.

    Used only for *collecting tracked assignments* so that a connection opened
    inside a nested function is attributed to that nested function (which is
    analysed separately), never to the outer one.
    """
    out: list[ast.AST] = []
    stack = list(getattr(func, "body", []))
    while stack:
        node = stack.pop()
        out.append(node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _FUNC_TYPES + (ast.ClassDef, ast.Lambda)):
                continue
            stack.append(child)
    return out


def _close_calls(var: str, calls: list[tuple[ast.Call, bool]]) -> list[tuple[int, bool]]:
    """(line, inside_finally) of every ``var.close()`` call."""
    hits = []
    for node, fin in calls:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "close" \
                and isinstance(func.value, ast.Name) and func.value.id == var:
            hits.append((node.lineno, fin))
    return hits


def _transfer_reason(var: str, returns: list, yields: list, assign_values: list, calls: list[tuple[ast.Call,
                                                                                                   bool]]) -> str:
    """Non-empty reason string when ownership of ``var`` leaves the function."""
    for node in returns:
        if var in _direct_names(node.value):
            return "returned to the caller"
    for value in yields:
        if var in _direct_names(value):
            return "yielded to the caller"
    for value in assign_values:
        if var in _direct_names(value):
            return "stored/aliased by an assignment"
    for node, _fin in calls:
        arg_exprs = list(node.args) + [kw.value for kw in node.keywords]
        for arg in arg_exprs:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Name) and sub.id == var:
                    return f"passed to {call_name(node.func) or 'a call'}()"
    return ""


def _fix_with_closing(line_text: str, var: str) -> dict:
    """closing()-based fix built from the real assignment line."""
    before = line_text.strip()
    _lhs, _sep, rhs = before.partition("=")
    rhs = rhs.strip() or "<open call>"
    return {
        "before": before,
        "after": f"with contextlib.closing({rhs}) as {var}:",
    }


def run(files: list[FileCtx], mode: str, context: dict) -> list[dict]:
    """Entry point, see the module docstring for the rule set."""
    del mode, context  # behaviour is identical in repo and diff-only modes
    findings: list[dict] = []
    for ctx in files or []:
        if ctx.change_type in ("deleted", "binary") or ctx.language != "python":
            continue
        if not ctx.candidate_lines or ctx.content is None:
            continue
        tree, _err = ctx.parse_ast()
        if tree is None:
            # Partial diff-only content or a syntax error: degrade, never raise.
            findings.extend(_regex_fallback(ctx))
            continue
        imports = _collect_imports(tree)
        for node in ast.walk(tree):
            if isinstance(node, _FUNC_TYPES):
                findings.extend(_scan_function(ctx, node, imports))
    return findings


def _scan_function(ctx: FileCtx, func: ast.AST, imports: _Imports) -> list[dict]:
    """Apply DB001-DB005 to one function definition."""
    findings: list[dict] = []
    fname = getattr(func, "name", "<func>")
    flagged = _flagged_walk(func)
    calls = [(node, fin) for node, fin in flagged if isinstance(node, ast.Call)]
    returns = [node for node, _f in flagged if isinstance(node, ast.Return) and node.value is not None]
    yields = [
        node.value for node, _f in flagged if isinstance(node, (ast.Yield, ast.YieldFrom)) and node.value is not None
    ]
    assign_values = []
    for node, _f in flagged:
        if isinstance(node, ast.Assign):
            assign_values.append(node.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            assign_values.append(node.value)
    global_names = {name for node, _f in flagged if isinstance(node, ast.Global) for name in node.names}

    # with-statement inventory: per-var management + transaction management
    with_ctx_ids: set[int] = set()
    with_name_vars: set[str] = set()
    txn_with = False
    for node, _f in flagged:
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                expr = item.context_expr
                with_ctx_ids.add(id(expr))
                if isinstance(expr, ast.Name):
                    with_name_vars.add(expr.id)
                    txn_with = True  # "with conn:" -> driver commits on success
                elif isinstance(expr, ast.Attribute):
                    txn_with = True  # "with self.conn:"
                elif isinstance(expr, ast.Call):
                    if _connect_call_kind(expr, imports):
                        txn_with = True  # "with sqlite3.connect(...) as conn:"
                    elif isinstance(expr.func, ast.Attribute) and expr.func.attr == "begin":
                        txn_with = True  # "with engine.begin():"

    # tracked assignments come from the function's own scope only
    conn_vars: list[tuple[str, ast.Call, int]] = []
    cursor_vars: list[tuple[str, ast.Call, int]] = []
    plain_assigns = []
    for node in _scope_nodes(func):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            plain_assigns.append((node.targets[0].id, node.value, node.lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            plain_assigns.append((node.target.id, node.value, node.lineno))
    seen_conn: set[str] = set()
    seen_cur: set[str] = set()
    for var, value, lineno in sorted(plain_assigns, key=lambda item: item[2]):
        if not isinstance(value, ast.Call):
            continue
        if _connect_call_kind(value, imports):
            if var not in seen_conn:  # first open wins; a re-open does not double-report
                seen_conn.add(var)
                conn_vars.append((var, value, lineno))
        elif isinstance(value.func, ast.Attribute) and value.func.attr == "cursor":
            if var not in seen_cur:
                seen_cur.add(var)
                cursor_vars.append((var, value, lineno))

    # ---- DB001 / DB005: connection lifecycle ----------------------------
    for var, call, line in conn_vars:
        if var in global_names or var in with_name_vars:
            continue  # module lifetime / with-managed: not reported
        if _transfer_reason(var, returns, yields, assign_values, calls):
            continue  # ownership left the function: not reported
        closes = _close_calls(var, calls)
        src = ctx.line_text(line).strip()
        if not closes:
            if not ctx.is_changed_line(line):
                continue
            findings.append(
                make_finding(
                    rule_id="DB001",
                    category=CATEGORY,
                    severity="high",
                    file=ctx.path,
                    line=line,
                    title="Database connection is never closed",
                    evidence=f"'{var}' opened at line {line} ({src}) in function '{fname}' has no "
                    f"{var}.close(), no 'with', and is never returned/stored/passed on",
                    recommendation=f"Close the connection on every path: wrap it in "
                    f"'with contextlib.closing(...)' or add a try/finally calling {var}.close().",
                    confidence="high",
                    precision="high",
                    fix_snippet=_fix_with_closing(ctx.line_text(line), var),
                ))
            continue
        # DB005 (mutually exclusive with DB001: a close exists) — risky only
        # when no close sits in a finally and a can-raise call intervenes.
        if any(fin for _line, fin in closes):
            continue
        first_close = min(close_line for close_line, _fin in closes)
        risky = False
        for node, _fin in calls:
            if node is call:
                continue
            func_attr = node.func
            if isinstance(func_attr, ast.Attribute) and func_attr.attr == "close" \
                    and isinstance(func_attr.value, ast.Name) and func_attr.value.id == var:
                continue
            if line < node.lineno < first_close:
                risky = True  # any call can raise -> leak on the exception path
                break
        if risky and ctx.is_changed_line(line):
            findings.append(
                make_finding(
                    rule_id="DB005",
                    category=CATEGORY,
                    severity="medium",
                    file=ctx.path,
                    line=line,
                    title="Connection close is not exception-safe",
                    evidence=f"'{var}' opened at line {line} is closed at line {first_close}, but calls "
                    f"between them can raise and the close is not inside a finally block",
                    recommendation="Move the close into a finally block (or use a with statement) so the "
                    "connection is released on the exception path too.",
                    confidence="medium",
                    precision="low",
                    fix_snippet={
                        "before": ctx.line_text(first_close).strip() or f"{var}.close()",
                        "after": f"try:\n    ...\nfinally:\n    {var}.close()",
                    },
                ))

    # ---- DB002: cursor lifecycle ----------------------------------------
    for var, call, line in cursor_vars:
        if var in global_names or var in with_name_vars:
            continue
        if _close_calls(var, calls):
            continue  # closed somewhere (even outside finally): GC backup makes nagging noise
        if _transfer_reason(var, returns, yields, assign_values, calls):
            continue
        if not ctx.is_changed_line(line):
            continue
        findings.append(
            make_finding(
                rule_id="DB002",
                category=CATEGORY,
                severity="medium",  # most drivers reclaim cursors on GC; harm is server handles
                file=ctx.path,
                line=line,
                title="Database cursor is never closed",
                evidence=f"'{var}' created at line {line} ({ctx.line_text(line).strip()}) in function "
                f"'{fname}' is never closed, with-managed or handed off",
                recommendation=f"Close the cursor deterministically, e.g. 'with contextlib.closing(...) "
                f"as {var}:' — relying on GC exhausts server-side handles under load.",
                confidence="medium",
                precision="high",
                fix_snippet=_fix_with_closing(ctx.line_text(line), var),
            ))

    # ---- DB004: dangling explicit transaction ---------------------------
    txn_ended = False
    for node, _fin in calls:
        func_attr = node.func
        name = ""
        if isinstance(func_attr, ast.Attribute):
            name = func_attr.attr
        elif isinstance(func_attr, ast.Name):
            name = func_attr.id
        if name in ("commit", "rollback"):
            txn_ended = True
            break
        if name in ("execute", "executemany") and _TXN_END_SQL_RE.match(_sql_literal(node)):
            txn_ended = True
            break
    db004_fired = False
    if not txn_ended:
        for node, _fin in calls:
            func_attr = node.func
            desc = ""
            if isinstance(func_attr, ast.Attribute) and func_attr.attr == "begin" \
                    and id(node) not in with_ctx_ids:
                desc = f"{call_name(func_attr) or 'begin'}()"
            elif isinstance(func_attr, ast.Attribute) and func_attr.attr in ("execute", "executemany"):
                literal = _sql_literal(node)
                if literal and _BEGIN_SQL_RE.match(literal):
                    desc = f"execute({literal.strip()[:30]!r})"
            if not desc or not ctx.is_changed_line(node.lineno):
                continue
            recv = conn_vars[0][0] if conn_vars else "conn"
            findings.append(
                make_finding(
                    rule_id="DB004",
                    category=CATEGORY,
                    severity="high",
                    file=ctx.path,
                    line=node.lineno,
                    title="Transaction begun but never committed or rolled back",
                    evidence=f"{desc} at line {node.lineno} in function '{fname}' with no commit()/"
                    f"rollback() and no COMMIT/ROLLBACK statement afterwards",
                    recommendation="Commit on success and roll back on error (try/except/else), or use "
                    "the connection as a context manager.",
                    confidence="high",
                    precision="high",
                    fix_snippet={
                        "before":
                        ctx.line_text(node.lineno).strip(),
                        "after":
                        f"{ctx.line_text(node.lineno).strip()}\ntry:\n    ...\n    {recv}.commit()\n"
                        f"except Exception:\n    {recv}.rollback()\n    raise",
                    },
                ))
            db004_fired = True
            break  # one dangling-transaction complaint per function is enough

    # ---- DB003: literal write statement without commit ------------------
    # Suppressed by: DB004 above (same root cause), commit/rollback evidence,
    # a transaction-managing with, or 'autocommit' in the function source.
    if db004_fired or txn_ended or txn_with:
        return findings
    end_line = getattr(func, "end_lineno", None) or func.lineno
    func_src = "\n".join(ctx.lines[func.lineno - 1:end_line])
    if _AUTOCOMMIT_RE.search(func_src):
        return findings
    write_hits: list[tuple[int, str, str]] = []
    for node, _fin in calls:
        func_attr = node.func
        if not (isinstance(func_attr, ast.Attribute) and func_attr.attr in ("execute", "executemany")):
            continue  # executescript() auto-commits first, deliberately skipped
        literal = _sql_literal(node)
        if not literal or not _WRITE_SQL_RE.match(literal):
            continue  # SELECT / PRAGMA / dynamic SQL: not a literal write
        receiver = func_attr.value.id if isinstance(func_attr.value, ast.Name) else ""
        write_hits.append((node.lineno, literal.strip().split(None, 1)[0].upper(), receiver))
    write_hits.sort()
    for line, verb, receiver in write_hits:
        if not ctx.is_changed_line(line):
            continue
        conn_name = _conn_name_hint(conn_vars, cursor_vars, receiver)
        src = ctx.line_text(line).strip()
        findings.append(
            make_finding(
                rule_id="DB003",
                category=CATEGORY,
                severity="high",
                file=ctx.path,
                line=line,
                title="Write statement may be executed without commit",
                evidence=f"{len(write_hits)} literal write statement(s) in function '{fname}', first "
                f"changed: {verb} at line {line}; no commit()/rollback(), no transaction 'with', "
                f"no autocommit in sight",
                recommendation=f"Call {conn_name}.commit() after the writes (or run them inside "
                f"'with {conn_name}:'); without it the changes are lost when the connection closes.",
                confidence="medium",
                precision="low",  # autocommit configured elsewhere is invisible to this check
                fix_snippet={
                    "before": src,
                    "after": f"{src}\n{conn_name}.commit()",
                },
            ))
        break  # one commit complaint per function; evidence carries the count
    return findings


def _conn_name_hint(conn_vars: list, cursor_vars: list, receiver: str) -> str:
    """Best-effort connection variable name for recommendations."""
    if conn_vars:
        return conn_vars[0][0]
    for var, call, _line in cursor_vars:
        if var == receiver and isinstance(call.func, ast.Attribute) \
                and isinstance(call.func.value, ast.Name):
            return call.func.value.id
    return "conn"


def _regex_fallback(ctx: FileCtx) -> list[dict]:
    """DB001-only heuristic for unparsable (partial) content.

    Scope is unknown without an AST, so the guards look at the whole visible
    text: any ``var.close()`` / ``with var`` / ``return var`` / ``yield var``
    suppresses.  precision=low, confidence=low by construction.
    """
    findings: list[dict] = []
    content = ctx.content or ""
    for line in sorted(ctx.candidate_lines):
        text = ctx.line_text(line)
        match = _CONNECT_ASSIGN_RE.match(text)
        if not match:
            continue
        var = match.group("var")
        escaped = re.escape(var)
        if re.search(rf"\b{escaped}\s*\.\s*close\s*\(", content):
            continue
        if re.search(rf"\bwith\s+{escaped}\b|\breturn\s+{escaped}\b|\byield\s+{escaped}\b", content):
            continue
        findings.append(
            make_finding(
                rule_id="DB001",
                category=CATEGORY,
                severity="high",
                file=ctx.path,
                line=line,
                title="Database connection may never be closed (diff-only heuristic)",
                evidence=f"line {line}: {text.strip()} — no {var}.close()/with/return visible in the "
                f"partial diff content",
                recommendation=f"Ensure the connection is closed on every path (with statement or "
                f"try/finally {var}.close()); full-repo mode can verify this precisely.",
                confidence="low",
                precision="low",
                fix_snippet=_fix_with_closing(text, var),
            ))
    return findings
