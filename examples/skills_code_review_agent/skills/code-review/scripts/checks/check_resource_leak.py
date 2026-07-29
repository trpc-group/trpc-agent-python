# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""resource_leak: OS resources acquired in changed code but never released.

The core asset of this module is a *function-level ownership analyzer*: for
every ``FunctionDef``/``AsyncFunctionDef`` (and the module top level) it
tracks local variables assigned from a resource-acquiring call and decides
whether the scope releases the resource, hands its ownership to someone else,
or silently drops it.  Only the last case is a finding.

Acquisition patterns (dotted names, import aliases resolved)
------------------------------------------------------------
* file-like:  ``open``, ``io.open``, ``codecs.open``, ``os.fdopen``,
  ``gzip/bz2/lzma.open``, ``tarfile.open``, ``zipfile.ZipFile``
* socket:     ``socket.socket``, ``socket.create_connection``
* temp file:  ``tempfile.NamedTemporaryFile``, ``tempfile.TemporaryFile``

Release evidence (any one of these silences RES001/RES003)
----------------------------------------------------------
(a) the call is a ``with`` item (``with open(p) as f:``) or the variable is
    later used as one (``with fh:``);
(b) ``var.close()`` exists in the same scope; when that close is *not* in a
    ``finally`` block and a ``return``/``raise`` sits between acquisition and
    close, the finding degrades to RES005 (exception-path leak, medium/low)
    instead of RES001;
(c) ownership transfer -- see below.

Ownership transfer -- patterns deliberately NOT reported (FP guards)
--------------------------------------------------------------------
* ``return var`` / ``yield var`` (also inside tuples/lists/dicts/ternaries):
  the caller owns the resource now;
* stored into an object or container: ``self.fh = var``, ``cache[k] = var``,
  ``handles = [var, ...]`` -- and direct ``self.fh = open(...)`` is never even
  registered (the instance owns it, a ``close()`` method elsewhere is fine);
* passed as an argument to any call, however nested: ``sink.register(fh)``,
  ``json.load(open(p))``, ``with closing(fh):``, ``os.unlink(tmp.name)``;
* aliased to another name (``g = fh``) -- alias lifetime is untrackable;
* assigned to a declared ``global`` name, or referenced inside a nested
  function/class (the closure may close it later);
* module-level ``LOG = open(...)`` style globals: process-lifetime handles
  are treated as owned by the module, not leaked (RES002/004/006 still apply
  at top level);
* class-body assignments, tuple-unpacked or multi-target assignments and
  bare unassigned ``open(p)`` statements are not tracked (miss, not noise).

Rules (severity/precision)
--------------------------
RES001  file/temp handle acquired, never released           high/high
RES002  chained ``open(...).read()`` use-and-discard: CPython refcounting
        closes it, PyPy and exception paths do not          low/high (conf=medium)
RES003  socket acquired, never released                     high/high
RES004  ``lock.acquire()`` without ``.release()`` in the same scope for a
        threading Lock/RLock/Semaphore (ctor-confirmed or lock-ish name);
        ``with lock:`` compiles to no ``acquire`` call and never fires.
        Cross-method pairs (``def lock/unlock``) and self-receiver wrapper
        methods named acquire/lock/enter/hold are skipped   high/high
RES005  close exists but an early return/raise sits between acquisition and
        close outside try/finally                           medium/low
RES006  ``NamedTemporaryFile(delete=False)`` and the scope neither unlinks
        the file nor hands the object/path to anyone        medium/low

Diff-only robustness
--------------------
Ownership analysis needs the whole function body.  When ``content_complete``
is False (gap-reconstructed post-image: a ``close()`` in an unseen context
line would be invisible) only RES002 runs -- via AST when the partial text
still parses, else via a per-changed-line regex (precision=low).  Half a
function cannot prove a leak, so RES001/003/004/005/006 honestly stand down.
Nothing in this module raises on partial content; every finding is anchored
on a changed line (``ctx.is_changed_line``) at the resource-acquisition call.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from checks.common import FileCtx, call_name, make_finding

CATEGORY = "resource_leak"

# ---------------------------------------------------------------------------
# acquisition tables (resolved dotted names; bare names cover from-imports
# that are outside the visible content)
# ---------------------------------------------------------------------------

_FILE_CALLS = frozenset({
    "open",
    "io.open",
    "codecs.open",
    "os.fdopen",
    "gzip.open",
    "bz2.open",
    "lzma.open",
    "tarfile.open",
    "zipfile.ZipFile",
    "ZipFile",
})
_SOCKET_CALLS = frozenset({
    "socket.socket",
    "socket.create_connection",
    "socket",
    "create_connection",
})
_TEMPFILE_CALLS = frozenset({
    "tempfile.NamedTemporaryFile",
    "tempfile.TemporaryFile",
    "NamedTemporaryFile",
    "TemporaryFile",
})
_LOCK_CTOR_CALLS = frozenset({
    "threading.Lock",
    "threading.RLock",
    "threading.Semaphore",
    "threading.BoundedSemaphore",
    "threading.Condition",
    "multiprocessing.Lock",
    "multiprocessing.RLock",
    "multiprocessing.Semaphore",
    "multiprocessing.BoundedSemaphore",
    "Lock",
    "RLock",
    "Semaphore",
    "BoundedSemaphore",
    "Condition",
})
#: modules whose import aliases are worth resolving
_INTEREST_MODULES = frozenset({
    "io",
    "os",
    "codecs",
    "gzip",
    "bz2",
    "lzma",
    "tarfile",
    "zipfile",
    "socket",
    "tempfile",
    "threading",
    "multiprocessing",
})
#: function names that look like deliberate lock-wrapper methods
_WRAPPER_NAME_RE = re.compile(r"(?i)(?:acquire|lock|enter|hold)")
#: regex fallback for RES002 when no AST is available (one nesting level of parens)
_RES002_LINE_RE = re.compile(r"(?<![\w.])(?P<call>(?:(?:gzip|bz2|lzma|tarfile|io|codecs)\s*\.\s*open|open"
                             r"|zipfile\s*\.\s*ZipFile|ZipFile)\s*\((?:[^()]|\([^()]*\))*\))"
                             r"\s*\.\s*(?!close\b)(?P<meth>\w+)\s*\(")

_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPE_BOUNDARY_TYPES = _FUNC_TYPES + (ast.ClassDef, )
_TRY_TYPES = (ast.Try, ) + ((ast.TryStar, ) if hasattr(ast, "TryStar") else ())
_MATCH_CASE = getattr(ast, "match_case", None)


def _classify(resolved: str):
    """Map a resolved dotted call name to (kind, rule) or None."""
    if resolved in _FILE_CALLS:
        return "file", "RES001"
    if resolved in _SOCKET_CALLS:
        return "socket", "RES003"
    if resolved in _TEMPFILE_CALLS:
        return "tempfile", "RES001"
    return None


def _build_alias_map(tree: ast.AST) -> dict:
    """local name -> canonical dotted prefix, for the modules we care about."""
    amap: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _INTEREST_MODULES:
                    amap[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module in _INTEREST_MODULES and not node.level:
                for alias in node.names:
                    amap[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return amap


def _resolve(name: str, alias_map: dict) -> str:
    """Rewrite the first segment of a dotted name through the alias map."""
    if not name:
        return ""
    head, _sep, rest = name.partition(".")
    mapped = alias_map.get(head)
    if mapped:
        return f"{mapped}.{rest}" if rest else mapped
    return name


def _kw_is_false(call: ast.Call, kw_name: str) -> bool:
    """True when the call passes ``kw_name=False`` as a literal keyword."""
    for kw in call.keywords:
        if kw.arg == kw_name and isinstance(kw.value, ast.Constant) and kw.value.value is False:
            return True
    return False


def _direct_names(expr) -> set:
    """Names whose *object itself* escapes through the expression.

    Value positions only: bare names, tuple/list/set/dict elements, starred,
    ternary branches, boolean operands, walrus/await payloads.  ``fh.read()``
    exposes data, not the handle, so attribute/call bases do NOT count here
    (call *arguments* are handled separately as transfers).
    """
    out: set = set()
    stack = [expr]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            stack.extend(node.elts)
        elif isinstance(node, ast.Dict):
            stack.extend(k for k in node.keys if k is not None)
            stack.extend(node.values)
        elif isinstance(node, ast.Starred):
            stack.append(node.value)
        elif isinstance(node, ast.IfExp):
            stack.extend((node.body, node.orelse))
        elif isinstance(node, ast.BoolOp):
            stack.extend(node.values)
        elif isinstance(node, ast.NamedExpr):
            stack.append(node.value)
        elif isinstance(node, ast.Await):
            stack.append(node.value)
    return out


def _all_names(expr) -> set:
    """Every Name id anywhere inside the expression (deep walk)."""
    if expr is None:
        return set()
    return {n.id for n in ast.walk(expr) if isinstance(n, ast.Name)}


def _lockish_name(receiver: str) -> bool:
    """Heuristic: does the receiver's last segment look like a lock?"""
    last = receiver.split(".")[-1].lower().strip("_")
    if "lock" in last or "mutex" in last or "semaphore" in last:
        return True
    return last in ("sem", "sema") or last.startswith(("sem_", "sema_")) or last.endswith(("_sem", "_sema"))


@dataclass
class _Acq:
    """One tracked resource acquisition bound to a local variable."""
    var: str
    node: ast.Call
    kind: str  # file | socket | tempfile
    rule: str  # RES001 | RES003
    delete_false: bool = False


@dataclass
class _Scope:
    """Everything the ownership analyzer learned about one scope."""
    name: str
    module_level: bool
    alias_map: dict
    acqs: list = field(default_factory=list)
    temp_records: list = field(default_factory=list)  # (var, call node, managed_by_with)
    closes: dict = field(default_factory=dict)  # var -> [(line, in_finally)]
    transfers: set = field(default_factory=set)
    with_released: set = field(default_factory=set)
    deep_returned: set = field(default_factory=set)  # names anywhere in return/yield exprs
    return_raise_lines: list = field(default_factory=list)
    acquires: list = field(default_factory=list)  # (receiver, call node)
    releases: set = field(default_factory=set)
    local_lock_vars: set = field(default_factory=set)
    global_names: set = field(default_factory=set)
    returned_call_ids: set = field(default_factory=set)
    has_unlink: bool = False


# ---------------------------------------------------------------------------
# scope collection
# ---------------------------------------------------------------------------


def _handle_assign_transfer(scope: _Scope, targets: list, value) -> None:
    """Record ownership transfers implied by an assignment statement."""
    names = _direct_names(value)
    if not names:
        return
    for target in targets:
        if isinstance(target, (ast.Attribute, ast.Subscript, ast.Tuple, ast.List)):
            scope.transfers |= names  # stored into object/container: owner changed
        elif isinstance(target, ast.Name):
            scope.transfers |= names - {target.id}  # alias: lifetime untrackable
            if target.id in scope.global_names:
                scope.transfers |= names  # parked in a global


def _handle_assign(scope: _Scope, st) -> None:
    """Register acquisitions and transfers for Assign/AnnAssign."""
    if isinstance(st, ast.Assign):
        targets, value = st.targets, st.value
    else:  # AnnAssign
        targets, value = [st.target], st.value
    if value is None:
        return
    if isinstance(value, ast.Call) and len(targets) == 1 and isinstance(targets[0], ast.Name):
        var = targets[0].id
        resolved = _resolve(call_name(value.func), scope.alias_map)
        cls = _classify(resolved)
        if resolved in _LOCK_CTOR_CALLS:
            scope.local_lock_vars.add(var)
        # a declared-global target parks the handle in module state: not tracked
        if cls and var not in scope.global_names:
            kind, rule = cls
            dfalse = kind == "tempfile" and _kw_is_false(value, "delete")
            scope.acqs.append(_Acq(var, value, kind, rule, dfalse))
            if dfalse:
                scope.temp_records.append((var, value, False))
    _handle_assign_transfer(scope, targets, value)


def _handle_with(scope: _Scope, st) -> None:
    """with-items: acquisitions here are managed; ``with var:`` releases var."""
    for item in st.items:
        ce = item.context_expr
        if isinstance(ce, ast.Name):
            scope.with_released.add(ce.id)
        elif isinstance(ce, ast.Call):
            resolved = _resolve(call_name(ce.func), scope.alias_map)
            cls = _classify(resolved)
            if cls and cls[0] == "tempfile" and _kw_is_false(ce, "delete") \
                    and isinstance(item.optional_vars, ast.Name):
                # handle is managed by the with, but the on-disk file survives
                scope.temp_records.append((item.optional_vars.id, ce, True))


def _own_expr_trees(st):
    """Expression subtrees owned by this statement (child statements excluded)."""
    for _fname, value in ast.iter_fields(st):
        if isinstance(value, ast.AST):
            if not isinstance(value, ast.stmt):
                yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST) and not isinstance(item, (ast.stmt, ast.ExceptHandler)) \
                        and not (_MATCH_CASE is not None and isinstance(item, _MATCH_CASE)):
                    yield item


def _scan_stmt_exprs(scope: _Scope, st, in_finally: bool) -> None:
    """Scan the statement's own expressions for closes/acquires/transfers."""
    for tree in _own_expr_trees(st):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    receiver = call_name(node.func.value)
                    attr = node.func.attr
                    if receiver:
                        if attr == "close":
                            scope.closes.setdefault(receiver, []).append((node.lineno, in_finally))
                        elif attr == "acquire":
                            scope.acquires.append((receiver, node))
                        elif attr == "release":
                            scope.releases.add(receiver)
                    if attr in ("unlink", "remove"):
                        scope.has_unlink = True
                if _resolve(call_name(node.func), scope.alias_map) in ("os.unlink", "os.remove"):
                    scope.has_unlink = True
                # any variable that flows into a call argument changes owner
                for arg in list(node.args) + [kw.value for kw in node.keywords]:
                    scope.transfers |= _all_names(arg)
            elif isinstance(node, (ast.Yield, ast.YieldFrom)):
                value = getattr(node, "value", None)
                scope.transfers |= _direct_names(value)
                scope.deep_returned |= _all_names(value)


def _visit(scope: _Scope, stmts: list, in_finally: bool) -> None:
    """Linear walk of a statement block, not descending into nested scopes."""
    for st in stmts:
        if isinstance(st, _SCOPE_BOUNDARY_TYPES):
            # a var referenced inside a nested def/class escapes this scope:
            # the closure/instance may close it later -> ownership transfer
            scope.transfers |= {n.id for n in ast.walk(st) if isinstance(n, ast.Name)}
            continue
        if isinstance(st, (ast.Global, ast.Nonlocal)):
            scope.global_names.update(st.names)
        elif isinstance(st, (ast.Assign, ast.AnnAssign)):
            _handle_assign(scope, st)
        elif isinstance(st, ast.AugAssign):
            scope.transfers |= _direct_names(st.value)
        elif isinstance(st, ast.Return):
            scope.return_raise_lines.append(st.lineno)
            scope.transfers |= _direct_names(st.value)
            scope.deep_returned |= _all_names(st.value)
            if isinstance(st.value, ast.Call):
                scope.returned_call_ids.add(id(st.value))
        elif isinstance(st, ast.Raise):
            scope.return_raise_lines.append(st.lineno)
        elif isinstance(st, (ast.With, ast.AsyncWith)):
            _handle_with(scope, st)
        _scan_stmt_exprs(scope, st, in_finally)

        if isinstance(st, _TRY_TYPES):
            _visit(scope, st.body, in_finally)
            for handler in st.handlers:
                _visit(scope, handler.body, in_finally)
            _visit(scope, st.orelse, in_finally)
            _visit(scope, st.finalbody, True)
            continue
        for _fname, value in ast.iter_fields(st):
            if not isinstance(value, list):
                continue
            child_stmts = [v for v in value if isinstance(v, ast.stmt)]
            if child_stmts:
                _visit(scope, child_stmts, in_finally)
            if _MATCH_CASE is not None:
                for v in value:
                    if isinstance(v, _MATCH_CASE):
                        _visit(scope, v.body, in_finally)


def _collect_scopes(tree: ast.Module, alias_map: dict) -> list:
    """Module top level plus every function anywhere in the tree."""
    scopes = [_Scope("<module>", True, alias_map)]
    _visit(scopes[0], tree.body, False)
    for node in ast.walk(tree):
        if isinstance(node, _FUNC_TYPES):
            scope = _Scope(node.name, False, alias_map)
            _visit(scope, node.body, False)
            scopes.append(scope)
    return scopes


# ---------------------------------------------------------------------------
# finding construction
# ---------------------------------------------------------------------------


def _anchor(ctx: FileCtx, node: ast.AST):
    """First changed line inside the node's span, or None (then: no finding)."""
    start = getattr(node, "lineno", 0) or 0
    end = getattr(node, "end_lineno", start) or start
    for line in range(start, end + 1):
        if ctx.is_changed_line(line):
            return line
    return None


def _segment(ctx: FileCtx, node: ast.AST) -> str:
    """Source text of the node, whitespace-collapsed; line text as fallback."""
    seg = None
    try:
        seg = ast.get_source_segment(ctx.content or "", node)
    except Exception:  # pylint: disable=broad-except
        seg = None
    if seg:
        return " ".join(seg.split())
    return ctx.line_text(getattr(node, "lineno", 0)).strip()


def _res002_ast(ctx: FileCtx, tree: ast.Module, alias_map: dict) -> list:
    """RES002: ``open(...).read()`` chains -- AST-confirmed, whole tree."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "close":
            continue  # open(p).close() releases immediately: pointless, not a leak
        inner = node.func.value
        if not isinstance(inner, ast.Call):
            continue
        cls = _classify(_resolve(call_name(inner.func), alias_map))
        if not cls or cls[0] == "socket":
            continue  # chained socket calls are not the use-and-discard idiom
        anchor = _anchor(ctx, node)
        if anchor is None:
            continue
        inner_src = _segment(ctx, inner)
        parts = [_segment(ctx, a) for a in node.args]
        parts += [(f"{kw.arg}={_segment(ctx, kw.value)}" if kw.arg else f"**{_segment(ctx, kw.value)}")
                  for kw in node.keywords]
        out.append(
            make_finding(
                rule_id="RES002",
                category=CATEGORY,
                severity="low",
                file=ctx.path,
                line=anchor,
                title="Chained open-and-discard relies on the garbage collector",
                evidence=f"{_segment(ctx, node)} -- the handle from {inner_src} is never bound, so only "
                "CPython refcounting closes it; PyPy and exception paths leak it",
                recommendation="Bind the resource in a with-statement so it is closed deterministically.",
                confidence="medium",
                precision="high",
                fix_snippet={
                    "before": _segment(ctx, node),
                    "after": f"with {inner_src} as _fh:\n    result = _fh.{node.func.attr}({', '.join(parts)})",
                },
            ))
    return out


def _res002_regex(ctx: FileCtx) -> list:
    """Regex fallback for RES002 when no AST is available (diff-only gaps)."""
    out = []
    for line in sorted(ctx.candidate_lines):
        text = ctx.line_text(line)
        if text.lstrip().startswith("#"):
            continue
        match = _RES002_LINE_RE.search(text)
        if not match:
            continue
        out.append(
            make_finding(
                rule_id="RES002",
                category=CATEGORY,
                severity="low",
                file=ctx.path,
                line=line,
                title="Chained open-and-discard relies on the garbage collector",
                evidence=f"{text.strip()} -- regex match on a changed line, AST unavailable "
                "(partial or unparsable content)",
                recommendation="Bind the resource in a with-statement so it is closed deterministically.",
                confidence="low",
                precision="low",
                fix_snippet={
                    "before": text.strip(),
                    "after": f"with {match.group('call')} as _fh:\n    _fh.{match.group('meth')}(...)",
                },
            ))
    return out


_KIND_LABEL = {"file": "file handle", "socket": "socket", "tempfile": "temporary file"}


def _score_scope(ctx: FileCtx, scope: _Scope, file_lock_vars: set, module_release_recvs: set, out: list) -> None:
    """Turn one scope's ownership facts into findings (RES001/3/4/5/6)."""
    res001_vars = set()

    # ---- RES001 / RES003 / RES005: tracked acquisitions -----------------
    for rec in scope.acqs:
        if rec.var in scope.transfers or rec.var in scope.with_released:
            continue  # ownership moved or with-managed: not this scope's leak
        events = scope.closes.get(rec.var, [])
        if any(fin for _line, fin in events):
            continue  # closed in a finally block: safe on every path
        later = sorted(line for line, _fin in events if line >= rec.node.lineno)
        anchor = _anchor(ctx, rec.node)
        label = _KIND_LABEL[rec.kind]
        call_src = _segment(ctx, rec.node)
        if later:
            risky = sorted(r for r in scope.return_raise_lines if rec.node.lineno < r < later[0])
            if not risky or anchor is None:
                continue  # plain close after acquisition, no early exit between
            out.append(
                make_finding(
                    rule_id="RES005",
                    category=CATEGORY,
                    severity="medium",
                    file=ctx.path,
                    line=anchor,
                    title=f"{label.capitalize()} may leak on an early-exit path",
                    evidence=f"'{rec.var}' acquired at line {rec.node.lineno} ({call_src}), closed at line "
                    f"{later[0]} outside finally, but a return/raise at line {risky[0]} can skip the close",
                    recommendation="Move the close() into a finally block or manage the resource "
                    "with a with-statement.",
                    confidence="medium",
                    precision="low",
                    fix_snippet={
                        "before":
                        ctx.line_text(rec.node.lineno).strip(),
                        "after":
                        f"{rec.var} = {call_src}\ntry:\n    ...  # body incl. early returns\n"
                        f"finally:\n    {rec.var}.close()",
                    },
                ))
            continue
        # no close at/after the acquisition in this scope
        if scope.module_level:
            continue  # module-global handle: process-lifetime ownership (see docstring)
        if anchor is None:
            continue
        extra = " (delete=False: the temp file also stays on disk)" if rec.delete_false else ""
        res001_vars.add(rec.var)
        out.append(
            make_finding(
                rule_id=rec.rule,
                category=CATEGORY,
                severity="high",
                file=ctx.path,
                line=anchor,
                title=f"{label.capitalize()} is never closed",
                evidence=f"'{rec.var} = {call_src}' in {scope.name}(): no with, no {rec.var}.close(), and "
                f"ownership never leaves the function{extra}",
                recommendation="Use a with-statement (or close() in a finally block) so the "
                f"{label} is released on every path.",
                confidence="high",
                precision="high",
                fix_snippet={
                    "before": ctx.line_text(rec.node.lineno).strip(),
                    "after": f"with {call_src} as {rec.var}:\n    ...  # use {rec.var} here",
                },
            ))

    # ---- RES006: NamedTemporaryFile(delete=False) left on disk ----------
    for var, node, managed in scope.temp_records:
        if var in res001_vars:
            continue  # the stronger handle-leak finding already covers this site
        if var in scope.transfers or var in scope.deep_returned:
            continue  # object or its .name escaped: the receiver cleans up
        if scope.has_unlink:
            continue
        anchor = _anchor(ctx, node)
        if anchor is None:
            continue
        before = ctx.line_text(node.lineno).strip()
        if managed:
            after = f"{before}\n    ...\nos.unlink({var}.name)  # delete=False keeps the file on disk"
        else:
            after = f"{before}\ntry:\n    ...\nfinally:\n    {var}.close()\n    os.unlink({var}.name)"
        out.append(
            make_finding(
                rule_id="RES006",
                category=CATEGORY,
                severity="medium",
                file=ctx.path,
                line=anchor,
                title="NamedTemporaryFile(delete=False) is never removed",
                evidence=f"{_segment(ctx, node)} in {scope.name}(): delete=False keeps the file after close, "
                "and no os.unlink/os.remove in this scope, nor does the path escape to a caller",
                recommendation="os.unlink(tmp.name) in a finally block once the file is no longer "
                "needed, or drop delete=False.",
                confidence="medium",
                precision="low",
                fix_snippet={
                    "before": before,
                    "after": after
                },
            ))

    # ---- RES004: lock.acquire() without release --------------------------
    for receiver, node in scope.acquires:
        if receiver in scope.releases:
            continue
        ctor_known = receiver in file_lock_vars or receiver in scope.local_lock_vars
        if not ctor_known and not _lockish_name(receiver):
            continue  # pool.acquire() etc: not provably a threading lock
        if receiver in module_release_recvs and receiver not in scope.local_lock_vars:
            continue  # cross-method acquire/release protocol (e.g. lock()/unlock() pair)
        if receiver.startswith("self.") and _WRAPPER_NAME_RE.search(scope.name):
            continue  # deliberate wrapper method delegating acquire to callers
        if id(node) in scope.returned_call_ids and receiver.startswith("self."):
            continue  # `return self._lock.acquire(...)` wrapper API
        anchor = _anchor(ctx, node)
        if anchor is None:
            continue
        out.append(
            make_finding(
                rule_id="RES004",
                category=CATEGORY,
                severity="high",
                file=ctx.path,
                line=anchor,
                title="Lock acquired without a matching release",
                evidence=f"{_segment(ctx, node)} in {scope.name}(): no {receiver}.release() anywhere in "
                "the scope" + (" (constructed from threading in this file)" if ctor_known else ""),
                recommendation=f"Use 'with {receiver}:' or release in a finally block; a leaked lock "
                "deadlocks every later acquirer.",
                confidence="high" if ctor_known else "medium",
                precision="high",
                fix_snippet={
                    "before": ctx.line_text(node.lineno).strip(),
                    "after": f"with {receiver}:\n    ...  # critical section",
                },
            ))


def _analyze_file(ctx: FileCtx) -> list:
    """All findings for one file; never raises (driver isolates us anyway)."""
    tree, _err = ctx.parse_ast()
    if tree is None:
        # diff-only gap reconstruction (or a syntax error): half a function
        # cannot prove a leak -> only the line-local RES002 pattern, by regex.
        return _res002_regex(ctx)
    alias_map = _build_alias_map(tree)
    out = _res002_ast(ctx, tree, alias_map)
    if not ctx.content_complete:
        # AST parsed but unseen gap lines could hide a close()/release():
        # claiming RES001/003/004/005/006 here would fabricate precision.
        return out
    file_lock_vars = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call) \
                and _resolve(call_name(node.value.func), alias_map) in _LOCK_CTOR_CALLS:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                dotted = call_name(target)
                if dotted:
                    file_lock_vars.add(dotted)
    scopes = _collect_scopes(tree, alias_map)
    module_release_recvs = set()
    for scope in scopes:
        module_release_recvs |= scope.releases
    for scope in scopes:
        _score_scope(ctx, scope, file_lock_vars, module_release_recvs, out)
    return out


def run(files: list, mode: str, context: dict) -> list:  # pylint: disable=unused-argument
    """Entry point, see the module docstring for the rule set."""
    findings = []
    for ctx in files or []:
        if ctx.language != "python" or ctx.change_type in ("deleted", "binary"):
            continue
        if not ctx.content or not ctx.candidate_lines:
            continue
        try:
            findings.extend(_analyze_file(ctx))
        except Exception:  # pylint: disable=broad-except
            continue  # a broken file must never take the whole check down
    findings.sort(key=lambda f: (f["file"], f["line"], f["rule_id"]))
    return findings
