# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""async: event-loop hazards -- blocking calls, lost coroutines and leaked sessions.

AST-first check for python files only.  Call targets are resolved through the
file's real import statements (``import time as t`` and ``from time import
sleep`` both map to ``time.sleep``), and every finding anchors on a line inside
``candidate_lines`` so pre-existing code is never re-reported.

Rules (severity/precision/confidence)
-------------------------------------
ASYNC001  blocking call inside ``async def``                     high/high/high
          time.sleep, requests.get/post/put/delete/head/request,
          urllib.request.urlopen, subprocess.run/call/check_call/
          check_output, socket.create_connection executed in the
          async function's own frame.
ASYNC002  coroutine created but never awaited                    high/high/high
          a bare expression statement calling an ``async def``
          defined in this file (bare name or ``self.<method>``),
          or a bare ``asyncio.sleep(...)``.
ASYNC003  task reference discarded                             medium/high/high
          bare-statement ``asyncio.create_task(...)`` /
          ``asyncio.ensure_future(...)`` / ``<loop>.create_task``:
          the loop keeps only weak references, so the CPython docs
          require saving the result or the task may be
          garbage-collected mid-flight.
ASYNC004  sync ``open()`` in an ``async def`` that awaits      medium/low/medium
          heuristic for file IO on the event-loop thread; low
          precision on purpose, the decision table routes this
          tier into the warnings bucket.
ASYNC005  ``aiohttp.ClientSession()`` neither ``async with``      high/high/high
          scoped nor closed in the creating function.

Patterns deliberately NOT reported (false-positive guards)
----------------------------------------------------------
* ASYNC001: calls inside a nested sync ``def`` or ``lambda`` (they run only
  when the inner callable is invoked, typically via run_in_executor /
  to_thread); bare names that no import binds to a blocking module (a local
  helper called ``sleep``/``run``/``get`` stays silent -- only ``from time
  import sleep`` style imports promote bare names); attribute calls on other
  objects (``self.queue.get(...)``, ``session.get(...)`` never match).
* ASYNC002: awaited calls (``await coro()``); coroutines handed to
  ``asyncio.run`` / ``create_task`` / ``gather`` / ``ensure_future`` /
  ``TaskGroup.create_task`` -- only the statement-level call is inspected, an
  argument position means ownership transferred to the scheduler; assigned
  (``fut = coro()``) or returned coroutines (the caller owns them now).
* ASYNC003: ``task = asyncio.create_task(...)`` assignments; tasks appended
  to a list or passed to ``gather`` (argument position); ``tg.create_task``
  on a TaskGroup -- the group holds a strong reference, so only bases that
  are provably event loops count (name assigned from ``asyncio.get_event_loop``
  / ``get_running_loop`` / ``new_event_loop``, or a name ending in "loop").
* ASYNC004: dotted opens (``aiofiles.open``, ``io.open``, ``self.open``) --
  only the builtin bare ``open`` matches; ``open`` inside nested sync defs;
  async functions with no await/async-for/async-with (nothing suspends, so
  the heuristic has no event-loop contention to point at); files where the
  name ``open`` is rebound by an import.
* ASYNC005: ownership transfer -- the session is returned, yielded, stored
  into an attribute or subscript (``self.session = s``), passed to another
  callable as an argument, aliased to another name, used as a with-item
  later, or created at module level (long-lived singleton pattern; only
  function bodies are scanned); ``s.close()`` / ``s.aclose()`` anywhere in
  the enclosing function (nested defs included) silences the rule, as does
  creating the session directly in an ``async with`` / ``with`` item or in a
  larger expression whose owner we cannot see.

Diff-only robustness
--------------------
When ``parse_ast`` fails (gap-reconstructed content with ``content_complete=
False`` or a syntax error) the check degrades to a per-changed-line regex
pass driven by an indentation-based ``async def`` scope tracker; those
findings carry precision=low / confidence=low and the check never raises.
"""

from __future__ import annotations

import ast
import re

from checks.common import FileCtx, call_name, make_finding

CATEGORY = "async"

# ---------------------------------------------------------------------------
# import resolution
# ---------------------------------------------------------------------------


def _import_aliases(tree: ast.AST) -> dict:
    """Map local names to dotted import paths.

    ``import time`` -> {"time": "time"}; ``import urllib.request as ur`` ->
    {"ur": "urllib.request"}; ``from time import sleep`` -> {"sleep":
    "time.sleep"}.  Relative and star imports are ignored (never stdlib).
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    aliases[root] = root
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                if alias.name != "*":
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _resolve(name: str, aliases: dict) -> str:
    """Resolve the leading segment of a dotted call name through the imports.

    Unaliased dotted access keeps its spelling (``time.sleep`` still resolves
    in diff-only mode where the import line sits in a gap); bare names that
    no import binds stay bare, so local helpers never match module functions.
    """
    if not name:
        return ""
    head, _dot, rest = name.partition(".")
    base = aliases.get(head)
    if base is None:
        return name
    return f"{base}.{rest}" if rest else base


# ---------------------------------------------------------------------------
# shared AST helpers
# ---------------------------------------------------------------------------


def _own_scope_nodes(func_node: ast.AST):
    """Yield descendants that execute in the function's own frame.

    Never descends into nested ``def`` / ``async def`` / ``lambda``: their
    bodies run on another schedule (often handed to an executor), so blocking
    calls in there are not this frame's problem.  Comprehension bodies run
    immediately and stay included.
    """
    stack = list(getattr(func_node, "body", []))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _src(ctx: FileCtx, node: ast.AST) -> str:
    """Real source text of a node (whitespace collapsed), line text fallback."""
    seg = None
    try:
        seg = ast.get_source_segment(ctx.content or "", node)
    except Exception:  # pylint: disable=broad-except
        seg = None
    if seg:
        return " ".join(seg.split())
    return ctx.line_text(getattr(node, "lineno", 0)).strip()


def _call_args_src(node: ast.Call) -> str:
    """Argument list of a call re-rendered as source (``2``, ``url, timeout=5``)."""
    parts = [ast.unparse(a) for a in node.args]
    for kw in node.keywords:
        parts.append(f"{kw.arg}={ast.unparse(kw.value)}" if kw.arg else f"**{ast.unparse(kw.value)}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# ASYNC001: blocking calls inside async def
# ---------------------------------------------------------------------------

_REQUESTS_METHODS = ("get", "post", "put", "delete", "head", "request")
_BLOCKING_CALLS = frozenset({
    "time.sleep",
    "urllib.request.urlopen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "socket.create_connection",
} | {f"requests.{m}"
     for m in _REQUESTS_METHODS})


def _blocking_fix(resolved: str, node: ast.Call) -> str:
    """Concrete non-blocking rewrite for the matched call, using its real args."""
    args = _call_args_src(node)
    if resolved == "time.sleep":
        return f"await asyncio.sleep({args})"
    if resolved.startswith("requests."):
        method = resolved.split(".", 1)[1]
        return ("async with httpx.AsyncClient() as client:\n"
                f"    resp = await client.{method}({args})")
    if resolved == "urllib.request.urlopen":
        return ("async with aiohttp.ClientSession() as session:\n"
                f"    async with session.get({args}) as resp:\n"
                "        body = await resp.read()")
    if resolved == "socket.create_connection":
        return f"reader, writer = await asyncio.open_connection({args})"
    # subprocess.*: least invasive correct rewrite is a worker thread
    return f"await asyncio.to_thread({resolved}, {args})"


def _blocking_reco(resolved: str) -> str:
    if resolved == "time.sleep":
        return ("Use 'await asyncio.sleep(...)' so the event loop keeps serving other tasks "
                "while this coroutine waits.")
    if resolved.startswith("requests.") or resolved == "urllib.request.urlopen":
        return ("Use an async HTTP client (aiohttp / httpx.AsyncClient) or off-load the call with "
                "'await asyncio.to_thread(...)'; a synchronous request stalls every task on the loop.")
    if resolved == "socket.create_connection":
        return ("Use 'await asyncio.open_connection(...)' (or loop.sock_connect) instead of a "
                "blocking socket call on the event-loop thread.")
    return ("Use asyncio.create_subprocess_exec/create_subprocess_shell, or run the blocking call "
            "in a worker thread via 'await asyncio.to_thread(...)'.")


def _scan_blocking(ctx: FileCtx, tree: ast.AST, aliases: dict) -> list[dict]:
    findings: list[dict] = []
    for func in ast.walk(tree):
        if not isinstance(func, ast.AsyncFunctionDef):
            continue
        for node in _own_scope_nodes(func):
            if not isinstance(node, ast.Call):
                continue
            resolved = _resolve(call_name(node.func), aliases)
            if resolved not in _BLOCKING_CALLS or not ctx.is_changed_line(node.lineno):
                continue
            before = _src(ctx, node)
            findings.append(
                make_finding(
                    rule_id="ASYNC001",
                    category=CATEGORY,
                    severity="high",
                    file=ctx.path,
                    line=node.lineno,
                    title=f"Blocking call {resolved}() inside async function",
                    evidence=f"'{before}' runs on the event-loop thread of 'async def {func.name}' "
                    "and stalls every other task until it returns",
                    recommendation=_blocking_reco(resolved),
                    confidence="high",
                    precision="high",
                    fix_snippet={
                        "before": before,
                        "after": _blocking_fix(resolved, node)
                    },
                ))
    return findings


# ---------------------------------------------------------------------------
# ASYNC002: coroutine created but never awaited
# ---------------------------------------------------------------------------


def _collect_async_defs(tree: ast.AST) -> tuple[set, set]:
    """Names of async defs split into (bare-callable functions, class methods)."""
    method_nodes: set[int] = set()
    methods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.AsyncFunctionDef):
                    method_nodes.add(id(stmt))
                    methods.add(stmt.name)
    funcs = {
        node.name
        for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and id(node) not in method_nodes
    }
    return funcs, methods


def _scan_unawaited(ctx: FileCtx, tree: ast.AST, aliases: dict, async_funcs: set, async_methods: set) -> list[dict]:
    findings: list[dict] = []
    for node in ast.walk(tree):
        # only bare expression statements: awaited calls are ast.Await, and a
        # call in argument/assign/return position transfers ownership instead
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        name = call_name(call.func)
        target = ""
        if _resolve(name, aliases) == "asyncio.sleep":
            target = "asyncio.sleep"
        elif "." not in name and name in async_funcs:
            target = name
        elif name.startswith("self.") and name.count(".") == 1 and name[5:] in async_methods:
            target = name
        if not target or not ctx.is_changed_line(node.lineno):
            continue
        before = _src(ctx, call)
        findings.append(
            make_finding(
                rule_id="ASYNC002",
                category=CATEGORY,
                severity="high",
                file=ctx.path,
                line=node.lineno,
                title=f"Coroutine '{target}' is never awaited",
                evidence=f"bare statement '{before}' only creates a coroutine object and drops it; "
                "the body never runs (RuntimeWarning: coroutine was never awaited)",
                recommendation="Await the coroutine, or schedule it with asyncio.create_task(...) and keep "
                "the returned task reference.",
                confidence="high",
                precision="high",
                fix_snippet={
                    "before": before,
                    "after": f"await {before}"
                },
            ))
    return findings


# ---------------------------------------------------------------------------
# ASYNC003: fire-and-forget task reference discarded
# ---------------------------------------------------------------------------

_TASK_SPAWNERS = frozenset({"asyncio.create_task", "asyncio.ensure_future"})
_LOOP_FACTORIES = frozenset({
    "asyncio.get_event_loop",
    "asyncio.get_running_loop",
    "asyncio.new_event_loop",
})


def _collect_loop_vars(tree: ast.AST, aliases: dict) -> set:
    """Variable names assigned from an asyncio event-loop factory call."""
    loops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if _resolve(call_name(node.value.func), aliases) in _LOOP_FACTORIES:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        loops.add(target.id)
    return loops


def _spawned_task_name(call: ast.Call, aliases: dict, loop_vars: set) -> str:
    """Spawner label when the call schedules a task, "" otherwise.

    ``tg.create_task`` (TaskGroup) is deliberately excluded: the group keeps
    strong references, discarding its return value is fine.
    """
    resolved = _resolve(call_name(call.func), aliases)
    if resolved in _TASK_SPAWNERS:
        return resolved
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in ("create_task", "ensure_future"):
        base = func.value
        if isinstance(base, ast.Name) and (base.id in loop_vars or base.id.lower().endswith("loop")):
            return f"{base.id}.{func.attr}"
        if isinstance(base, ast.Call) and _resolve(call_name(base.func), aliases) in _LOOP_FACTORIES:
            return f"{call_name(base.func)}().{func.attr}"
    return ""


def _scan_discarded_tasks(ctx: FileCtx, tree: ast.AST, aliases: dict, loop_vars: set) -> list[dict]:
    findings: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        spawner = _spawned_task_name(node.value, aliases, loop_vars)
        if not spawner or not ctx.is_changed_line(node.lineno):
            continue
        before = _src(ctx, node.value)
        findings.append(
            make_finding(
                rule_id="ASYNC003",
                category=CATEGORY,
                severity="medium",
                file=ctx.path,
                line=node.lineno,
                title=f"Task from {spawner}() is discarded",
                evidence=f"'{before}' drops the returned Task; the event loop only keeps a weak "
                "reference, so the task can be garbage-collected before it finishes",
                recommendation="Save the task (variable, set + add_done_callback(discard), or "
                "asyncio.TaskGroup) as required by the CPython asyncio documentation.",
                confidence="high",
                precision="high",
                fix_snippet={
                    "before":
                    before,
                    "after": (f"task = {before}\n"
                              "background_tasks.add(task)\n"
                              "task.add_done_callback(background_tasks.discard)"),
                },
            ))
    return findings


# ---------------------------------------------------------------------------
# ASYNC004: sync open() inside an awaiting async def (heuristic)
# ---------------------------------------------------------------------------


def _scan_sync_open(ctx: FileCtx, tree: ast.AST, aliases: dict) -> list[dict]:
    findings: list[dict] = []
    if "open" in aliases:
        return findings  # `from x import open` rebinds the builtin: too ambiguous
    for func in ast.walk(tree):
        if not isinstance(func, ast.AsyncFunctionDef):
            continue
        own = list(_own_scope_nodes(func))
        # only flag functions that actually suspend: otherwise there is no
        # event-loop contention for the heuristic to point at
        if not any(isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for n in own):
            continue
        for node in own:
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"):
                continue
            if not ctx.is_changed_line(node.lineno):
                continue
            before = _src(ctx, node)
            findings.append(
                make_finding(
                    rule_id="ASYNC004",
                    category=CATEGORY,
                    severity="medium",
                    file=ctx.path,
                    line=node.lineno,
                    title="Synchronous open() inside awaiting async function",
                    evidence=f"'{before}' in 'async def {func.name}' does blocking file IO on the "
                    "event-loop thread while the function otherwise awaits",
                    recommendation="Use aiofiles, or move the file IO into a worker thread with "
                    "'await asyncio.to_thread(...)'.",
                    confidence="medium",
                    precision="low",
                    fix_snippet={
                        "before": before,
                        "after": f"async with aiofiles.{before} as fh:  # or: await asyncio.to_thread(...)",
                    },
                ))
    return findings


# ---------------------------------------------------------------------------
# ASYNC005: aiohttp.ClientSession neither scoped nor closed
# ---------------------------------------------------------------------------

_SESSION_TYPES = frozenset({"aiohttp.ClientSession"})


def _is_session_call(node: ast.AST, aliases: dict) -> bool:
    return isinstance(node, ast.Call) and _resolve(call_name(node.func), aliases) in _SESSION_TYPES


def _session_released(func: ast.AST, var: str) -> bool:
    """True when ownership of ``var`` leaves the function or it gets closed.

    Scans the whole function subtree (nested defs included) for: ``var.close()``
    / ``var.aclose()``, a with/async-with item on ``var``, ``var`` in a return
    or yield value, ``var`` passed as a call argument, ``var`` stored into an
    attribute/subscript, or ``var`` aliased to another name.
    """
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr in ("close", "aclose") and isinstance(f.value, ast.Name)
                    and f.value.id == var):
                return True
            args = list(node.args) + [kw.value for kw in node.keywords]
            for arg in args:
                if isinstance(arg, ast.Starred):
                    arg = arg.value
                if isinstance(arg, ast.Name) and arg.id == var:
                    return True
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.context_expr, ast.Name) and item.context_expr.id == var:
                    return True
        elif isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)) and node.value is not None:
            if any(isinstance(n, ast.Name) and n.id == var for n in ast.walk(node.value)):
                return True
        elif isinstance(node, ast.Assign):
            if any(isinstance(n, ast.Name) and n.id == var for n in ast.walk(node.value)):
                if any(not isinstance(t, ast.Name) for t in node.targets):
                    return True  # self.session = var / cache[k] = var
                if isinstance(node.value, ast.Name) and node.value.id == var:
                    return True  # plain aliasing blurs ownership: stay silent
    return False


def _scan_client_session(ctx: FileCtx, tree: ast.AST, aliases: dict) -> list[dict]:
    findings: list[dict] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # (var name | None, creation call) built from statement-level patterns
        # only; a session inside a larger expression (argument, container,
        # with-item, return) has its ownership elsewhere and stays silent.
        candidates: list[tuple] = []
        for node in _own_scope_nodes(func):
            if isinstance(node, ast.Assign) and _is_session_call(node.value, aliases):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    candidates.append((node.targets[0].id, node.value))
            elif isinstance(node, ast.AnnAssign) and node.value is not None \
                    and _is_session_call(node.value, aliases):
                if isinstance(node.target, ast.Name):
                    candidates.append((node.target.id, node.value))
            elif isinstance(node, ast.Expr) and _is_session_call(node.value, aliases):
                candidates.append((None, node.value))  # created and dropped on the spot
        for var, call in candidates:
            if var is not None and _session_released(func, var):
                continue
            if not ctx.is_changed_line(call.lineno):
                continue
            before = ctx.line_text(call.lineno).strip() or _src(ctx, call)
            name = var or "session"
            findings.append(
                make_finding(
                    rule_id="ASYNC005",
                    category=CATEGORY,
                    severity="high",
                    file=ctx.path,
                    line=call.lineno,
                    title="aiohttp.ClientSession neither scoped nor closed",
                    evidence=f"'{before}' in '{func.name}' creates a ClientSession that is never "
                    "closed, handed over or async-with scoped; its connector and sockets leak",
                    recommendation="Scope the session with 'async with aiohttp.ClientSession() as "
                    "session:' or guarantee 'await session.close()' on every path.",
                    confidence="high",
                    precision="high",
                    fix_snippet={
                        "before":
                        before,
                        "after": (f"async with aiohttp.ClientSession() as {name}:\n"
                                  f"    ...  # use {name} only inside this block"),
                    },
                ))
    return findings


# ---------------------------------------------------------------------------
# regex fallback when the AST is unavailable (diff-only gaps, syntax errors)
# ---------------------------------------------------------------------------

_RE_ASYNC_DEF = re.compile(r"^(\s*)async\s+def\s+([A-Za-z_]\w*)")
_RE_DEF = re.compile(r"^(\s*)def\s")
_RE_BLOCKING = re.compile(r"\b(?:time\.sleep|requests\.(?:get|post|put|delete|head|request)|urllib\.request\.urlopen"
                          r"|subprocess\.(?:run|call|check_call|check_output)|socket\.create_connection)\s*\(")
_RE_BARE_SLEEP = re.compile(r"^\s*asyncio\.sleep\s*\(")
_RE_BARE_SPAWN = re.compile(r"^\s*(?:asyncio\.(?:create_task|ensure_future)|[A-Za-z_]\w*(?<=loop)\.create_task)\s*\(")


def _fallback_finding(ctx: FileCtx,
                      rule_id: str,
                      line_no: int,
                      title: str,
                      evidence: str,
                      recommendation: str,
                      severity: str,
                      fix_snippet=None) -> dict:
    return make_finding(
        rule_id=rule_id,
        category=CATEGORY,
        severity=severity,
        file=ctx.path,
        line=line_no,
        title=title,
        evidence=f"{evidence} [regex fallback, AST unavailable]",
        recommendation=recommendation,
        confidence="low",
        precision="low",
        fix_snippet=fix_snippet,
    )


def _scan_regex_fallback(ctx: FileCtx) -> list[dict]:
    """Per-changed-line degraded scan; scope is guessed from indentation.

    Blank gap lines (diff-only reconstruction) and comment lines neither open
    nor close scopes, so partial hunks inside a visible ``async def`` header
    still classify correctly; changed lines whose header is hidden in a gap
    are skipped rather than guessed.
    """
    findings: list[dict] = []
    lines = ctx.lines
    async_names = sorted({m.group(2) for line in lines if (m := _RE_ASYNC_DEF.match(line))})
    bare_call_re = None
    if async_names:
        alternation = "|".join(re.escape(n) for n in async_names)
        bare_call_re = re.compile(rf"^\s*(?:self\.)?(?:{alternation})\s*\(")
    stack: list[tuple[int, bool]] = []  # (indent, is_async_def)
    for line_no, text in enumerate(lines, start=1):
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(text) - len(text.lstrip())
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if _RE_ASYNC_DEF.match(text):
            stack.append((indent, True))
            continue
        if _RE_DEF.match(text):
            stack.append((indent, False))
            continue
        if line_no not in ctx.candidate_lines:
            continue
        in_async = bool(stack) and stack[-1][1]
        blocking = _RE_BLOCKING.search(text)
        if in_async and blocking:
            fix = None
            if "time.sleep(" in stripped:
                fix = {"before": stripped, "after": stripped.replace("time.sleep(", "await asyncio.sleep(")}
            findings.append(
                _fallback_finding(ctx, "ASYNC001", line_no, "Possible blocking call inside async function",
                                  f"'{stripped}' looks like {blocking.group(0)}...) on the event loop",
                                  _blocking_reco(blocking.group(0).rstrip("(").strip()), "high", fix))
            continue
        if _RE_BARE_SLEEP.match(text) or (bare_call_re and bare_call_re.match(text)):
            findings.append(
                _fallback_finding(ctx, "ASYNC002", line_no, "Coroutine call is possibly never awaited",
                                  f"bare statement '{stripped}' seems to drop a coroutine object",
                                  "Await the coroutine or schedule it with asyncio.create_task(...).", "high", {
                                      "before": stripped,
                                      "after": f"await {stripped}"
                                  }))
            continue
        if _RE_BARE_SPAWN.match(text):
            findings.append(
                _fallback_finding(ctx, "ASYNC003", line_no, "Task reference is possibly discarded",
                                  f"bare statement '{stripped}' seems to drop the created Task",
                                  "Save the returned task so it cannot be garbage-collected mid-flight.", "medium"))
    return findings


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run(files: list[FileCtx], mode: str, context: dict) -> list[dict]:  # noqa: ARG001 (contract signature)
    """Entry point, see the module docstring for the rule set.

    ``mode`` needs no branching here: the AST -> regex degradation happens
    automatically whenever ``parse_ast`` fails on partial diff-only content.
    """
    findings: list[dict] = []
    for ctx in files or []:
        if ctx.language != "python" or ctx.change_type in ("deleted", "binary"):
            continue
        if ctx.content is None or not ctx.candidate_lines:
            continue  # pure renames / metadata-only changes
        tree, _err = ctx.parse_ast()
        if tree is None:
            findings.extend(_scan_regex_fallback(ctx))
            continue
        aliases = _import_aliases(tree)
        async_funcs, async_methods = _collect_async_defs(tree)
        loop_vars = _collect_loop_vars(tree, aliases)
        findings.extend(_scan_blocking(ctx, tree, aliases))
        findings.extend(_scan_unawaited(ctx, tree, aliases, async_funcs, async_methods))
        findings.extend(_scan_discarded_tasks(ctx, tree, aliases, loop_vars))
        findings.extend(_scan_sync_open(ctx, tree, aliases))
        findings.extend(_scan_client_session(ctx, tree, aliases))
    return findings
