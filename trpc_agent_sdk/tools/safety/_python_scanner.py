"""Python AST scanner that extracts ScriptFacts.

The scanner walks the parsed AST once, resolving import aliases so
``import requests as r; r.get(...)`` is recognized as a requests call.
Taint propagation is deliberately shallow: literals, names, direct
assignments, f-strings, concatenation, and shallow container
construction. Deeper flows surface as ``OBF001_DYNAMIC_EXEC`` instead
of a false claim of safety.
"""

from __future__ import annotations

import ast

from trpc_agent_sdk.tools.safety._facts import (
    ConcurrencyFact,
    DependencyInstallFact,
    DynamicExecFact,
    FileDeleteFact,
    FileReadFact,
    FileWriteFact,
    ForkBombFact,
    LargeWriteFact,
    Loc,
    LongSleepFact,
    NetworkFact,
    ParseErrorFact,
    PrivilegeFact,
    ProcessFact,
    ScriptFacts,
    SecretFlowFact,
    ShellOperatorFact,
    UnboundedLoopFact,
)
from trpc_agent_sdk.tools.safety._models import ScriptLanguage
from trpc_agent_sdk.tools.safety._rules import _LanguageScannerRule, SafetyRule
from trpc_agent_sdk.tools.safety._redaction import contains_secret_literal

# Networks libs and the attribute used to extract a host arg.
_NETWORK_LIBS: dict[str, tuple[str, ...]] = {
    "requests": ("get", "post", "put", "delete", "patch", "head", "options", "request"),
    "aiohttp": ("get", "post", "put", "delete", "patch", "head", "options", "request", "ClientSession"),
    "httpx": ("get", "post", "put", "delete", "patch", "head", "options", "request", "Client"),
    "urllib.request": ("urlopen", "urlretrieve", "Request"),
    "urllib": ("urlopen", ),
    "http.client": ("HTTPConnection", "HTTPSConnection"),
    "websocket": ("create_connection", ),
    "socket": ("socket", "connect", "create_connection"),
}

_PRIVILEGE_COMMANDS = {"sudo", "su", "doas", "pkexec"}

_PACKAGE_MANAGERS = {
    "pip": ("install", ),
    "pip3": ("install", ),
    "python": (),  # special-cased with -m pip
    "npm": ("install", "i", "add"),
    "yarn": ("add", "install"),
    "pnpm": ("add", "install"),
    "apt": ("install", ),
    "apt-get": ("install", ),
    "apk": ("add", ),
    "yum": ("install", ),
    "dnf": ("install", ),
    "brew": ("install", ),
    "conda": ("install", "create"),
}

# Names whose presence on the right-hand side of an assignment marks the
# left-hand name as carrying a secret.
_SECRET_SOURCE_NAMES = {
    "os.environ",
    "os.environ.get",
    "os.getenv",
    "environ",
    "environ.get",
    "getenv",
}

# --------------------------------------------------------------------------- #
# Source helpers
# --------------------------------------------------------------------------- #


def _src_segment(source: str, node: ast.AST) -> str:
    """Best-effort source segment for a node."""

    if not hasattr(node, "lineno") or node.lineno <= 0:
        return ""
    try:
        segment = ast.get_source_segment(source, node)
    except Exception:  # pragma: no cover - defensive
        segment = None
    if segment is None:
        return ""
    return segment.strip()


def _loc(node: ast.AST) -> Loc:
    line = getattr(node, "lineno", 0) or 0
    col = getattr(node, "col_offset", 0) or 0
    return Loc(line=line, column=col + 1)


def _const_str(node: ast.AST) -> str | None:
    """Return the string literal value if node is a constant string."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _const_int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def _format_string(node: ast.AST) -> str | None:
    """Return the static prefix of an f-string or concatenated string."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for value in node.values:
            text = _format_string(value)
            if text is None:
                out.append("{dynamic}")
            else:
                out.append(text)
        return "".join(out)
    if isinstance(node, ast.FormattedValue):
        return "{dynamic}"
    return None


# --------------------------------------------------------------------------- #
# Scanner
# --------------------------------------------------------------------------- #


class _PythonScanner:
    """Walks the AST once and collects facts."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.tree: ast.AST | None = None
        self.parse_errors: list[ParseErrorFact] = []
        self._alias_to_canonical: dict[str, str] = {}
        self._tainted: dict[str, str] = {}  # name -> source label
        self._path_aliases: dict[str, ast.AST] = {}
        self._loops_stack: list[ast.AST] = []

        # Accumulators
        self.file_deletes: list[FileDeleteFact] = []
        self.file_writes: list[FileWriteFact] = []
        self.file_reads: list[FileReadFact] = []
        self.network_calls: list[NetworkFact] = []
        self.process_calls: list[ProcessFact] = []
        self.shell_operators: list[ShellOperatorFact] = []
        self.privilege_commands: list[PrivilegeFact] = []
        self.dependency_installs: list[DependencyInstallFact] = []
        self.unbounded_loops: list[UnboundedLoopFact] = []
        self.fork_bombs: list[ForkBombFact] = []
        self.long_sleeps: list[LongSleepFact] = []
        self.concurrency: list[ConcurrencyFact] = []
        self.large_writes: list[LargeWriteFact] = []
        self.secret_flows: list[SecretFlowFact] = []
        self.dynamic_execs: list[DynamicExecFact] = []

    def parse(self) -> None:
        try:
            self.tree = ast.parse(self.source)
        except SyntaxError as exc:
            self.parse_errors.append(
                ParseErrorFact(
                    snippet="",
                    loc=Loc(line=exc.lineno or 0, column=exc.offset or 0),
                    message=f"SyntaxError: {exc.msg}",
                ))

    def scan(self) -> ScriptFacts:
        if self.tree is None:
            return ScriptFacts(
                language=ScriptLanguage.PYTHON,
                parse_errors=tuple(self.parse_errors),
            )
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    canon = alias.name
                    local = alias.asname or alias.name.split(".")[0]
                    self._alias_to_canonical[local] = canon
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    local = alias.asname or alias.name
                    canon = f"{module}.{alias.name}" if module else alias.name
                    self._alias_to_canonical[local] = canon
        self._collect_path_aliases()
        for node in ast.walk(self.tree):
            self._visit_any(node)
        return self._facts()

    def _collect_path_aliases(self) -> None:
        """Build name -> AST map for ``x = Path(...) / "literal"`` chains."""

        if self.tree is None:
            return
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if self._is_path_construction(node.value):
                    self._path_aliases[target.id] = node.value

    def _is_path_construction(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Call):
            canonical = self._canonical_name(node.func)
            return canonical in {"pathlib.Path", "Path", "PosixPath", "pathlib.PurePath", "pathlib.PurePosixPath"}
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return True
        return False

    # ----- generic dispatch ----- #

    def _visit_any(self, node: ast.AST) -> None:
        if isinstance(node, ast.Call):
            self._visit_call(node)
        elif isinstance(node, ast.Assign):
            self._visit_assign(node)
        elif isinstance(node, (ast.While, ast.For)):
            self._visit_loop(node)
        elif isinstance(node, ast.Subscript):
            self._visit_subscript(node)

    # ----- assignments / taint ----- #

    def _visit_assign(self, node: ast.Assign) -> None:
        source_label = self._secret_source_label(node.value)
        if source_label is None:
            return
        for target in node.targets:
            name = self._name_of(target)
            if name:
                self._tainted[name] = source_label

    def _visit_subscript(self, node: ast.AST) -> None:
        # os.environ["KEY"] reading -- mark nothing yet; taint flows from
        # later assignments handled above.
        pass

    def _secret_source_label(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Call):
            canonical = self._canonical_name(node.func)
            if canonical in _SECRET_SOURCE_NAMES:
                arg = node.args[0] if node.args else None
                key = _const_str(arg) or "<dynamic>"
                return f"env:{key}"
            # open(..., "r") on credential path
            if canonical in ("open", "io.open", "pathlib.Path.open", "codecs.open"):
                kind = self._credential_kind_for_open(node)
                if kind:
                    return f"file:{kind}"
        if isinstance(node, ast.Subscript):
            canonical = self._canonical_name(node.value)
            if canonical in ("os.environ", "environ"):
                key = _const_str(node.slice) or "<dynamic>"
                return f"env:{key}"
        return None

    def _credential_kind_for_open(self, call: ast.Call) -> str | None:
        if not call.args:
            return None
        target = _const_str(call.args[0])
        if not target:
            return None
        lowered = target.lower()
        if ".ssh" in lowered or "id_rsa" in lowered or "id_ecdsa" in lowered \
                or "id_ed25519" in lowered:
            return "credential"
        if "credentials" in lowered or ".netrc" in lowered \
                or "kubeconfig" in lowered or ".aws/credentials" in lowered:
            return "credential"
        if lowered.endswith(".pem") or lowered.endswith(".key") \
                or lowered.endswith(".p12") or lowered.endswith(".pfx"):
            return "credential"
        return None

    # ----- calls ----- #

    def _visit_call(self, node: ast.Call) -> None:
        canonical = self._canonical_name(node.func)
        if not canonical:
            return
        # 1) File operations
        if canonical in {"os.remove", "os.unlink"}:
            self.file_deletes.append(self._file_delete(node, recursive=False))
            return
        if canonical in {"os.rmdir", "pathlib.Path.rmdir", "pathlib.Path.unlink"}:
            self.file_deletes.append(self._file_delete(node, recursive=False))
            return
        if canonical == "shutil.rmtree":
            self.file_deletes.append(self._file_delete(node, recursive=True))
            return
        if canonical in {"pathlib.Path.write_text", "pathlib.Path.write_bytes"}:
            self.file_writes.append(self._pathlib_write(node, canonical))
            return
        if canonical in {"pathlib.Path.read_text", "pathlib.Path.read_bytes", "pathlib.Path.read"}:
            self._handle_pathlib_read(node)
            return
        # Fallback for ``path.read_text()`` where ``path`` is a local alias
        # assigned from a ``Path(...)`` construction.
        if isinstance(node.func, ast.Attribute) \
                and node.func.attr in {"read_text", "read_bytes", "read"} \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in self._path_aliases:
            self._handle_pathlib_read(node)
            return
        if canonical in {"open", "io.open", "codecs.open", "pathlib.Path.open"}:
            self._handle_open(node)
            return

        # 2) Network calls
        if self._handle_network_call(node, canonical):
            return

        # 3) Subprocess / shell
        if self._handle_process_call(node, canonical):
            return

        # 4) Dynamic exec
        if canonical in {"eval", "exec", "compile"}:
            self.dynamic_execs.append(
                DynamicExecFact(
                    snippet=_src_segment(self.source, node),
                    loc=_loc(node),
                    kind=canonical.split(".")[-1],
                ))
            return
        if canonical in {"importlib.import_module", "importlib.__import__", "__import__"}:
            self.dynamic_execs.append(
                DynamicExecFact(
                    snippet=_src_segment(self.source, node),
                    loc=_loc(node),
                    kind="dynamic_import",
                ))
            return
        if canonical == "getattr":
            # getattr(os, "system") -- only flag when the attribute name
            # is a string literal pointing at a dangerous primitive.
            if len(node.args) >= 2:
                attr = _const_str(node.args[1])
                if attr in {"system", "popen", "exec", "eval", "fork"}:
                    self.dynamic_execs.append(
                        DynamicExecFact(
                            snippet=_src_segment(self.source, node),
                            loc=_loc(node),
                            kind=f"getattr:{attr}",
                        ))
            return

        # 5) Sleep
        if canonical in {"time.sleep", "asyncio.sleep", "sleep"}:
            self._handle_sleep(node)
            return

        # 6) Concurrency primitives
        if self._handle_concurrency_call(node, canonical):
            return

        # 7) Output sinks (secret flow detection)
        self._handle_sink_call(node, canonical)

    # ----- file delete / write ----- #

    def _file_delete(self, node: ast.Call, *, recursive: bool) -> FileDeleteFact:
        target = ""
        explicit = True
        if node.args:
            target = _const_str(node.args[0]) or ""
            if not target:
                explicit = False
                target = self._name_of(node.args[0]) or "<dynamic>"
        return FileDeleteFact(
            snippet=_src_segment(self.source, node),
            loc=_loc(node),
            target=target,
            recursive=recursive,
            explicit=explicit,
        )

    def _pathlib_write(self, node: ast.Call, canonical: str) -> FileWriteFact:
        target = ""
        explicit = True
        # For pathlib.Path.write_text, the receiver is the path.
        if isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Call):
            target = _const_str(_first_arg(node.func.value)) or ""
        if not target:
            explicit = False
            target = "<dynamic>"
        return FileWriteFact(
            snippet=_src_segment(self.source, node),
            loc=_loc(node),
            target=target,
            mode="w",
            explicit=explicit,
        )

    def _handle_open(self, node: ast.Call) -> None:
        target = ""
        explicit = True
        if node.args:
            target = _const_str(node.args[0]) or ""
            if not target:
                explicit = False
                target = "<dynamic>"
        mode = "r"
        for arg in node.args[1:]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                mode = arg.value
                break
        if any(ch in mode for ch in ("w", "a", "x", "+")):
            self.file_writes.append(
                FileWriteFact(
                    snippet=_src_segment(self.source, node),
                    loc=_loc(node),
                    target=target,
                    mode=mode,
                    explicit=explicit,
                ))
        else:
            kind = self._credential_kind_for_str(target) or "regular"
            if target.lower().endswith(".env") or ".env" in target.lower():
                kind = "dotenv"
            self.file_reads.append(
                FileReadFact(
                    snippet=_src_segment(self.source, node),
                    loc=_loc(node),
                    target=target,
                    kind=kind,
                    explicit=explicit,
                ))

    def _handle_pathlib_read(self, node: ast.Call) -> None:
        """Handle ``Path(...).read_text()`` / ``read_bytes()``."""

        target = ""
        explicit = True
        # The receiver (Path(...) call) holds the path argument.
        receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
        # Resolve name aliases: ``path = Path(...) / ".ssh" / "id_rsa"``
        # then ``path.read_text()``.
        if isinstance(receiver, ast.Name) and receiver.id in self._path_aliases:
            receiver = self._path_aliases[receiver.id]
        path_arg: ast.AST | None = None
        if isinstance(receiver, ast.Call):
            path_arg = _first_arg(receiver)
            # Also collect any "/..." BinOp chain so we can see the full path
            extra = self._collect_path_chain(receiver)
            if extra:
                target = extra
        elif isinstance(receiver, ast.Constant) and isinstance(receiver.value, str):
            path_arg = receiver
            target = receiver.value
        elif isinstance(receiver, ast.JoinedStr):
            path_arg = receiver
        elif isinstance(receiver, ast.BinOp):
            target = self._collect_path_chain(receiver) or ""
        if not target and path_arg is not None:
            target = _const_str(path_arg) or _format_string(path_arg) or ""
        if not target:
            explicit = False
            target = "<dynamic>"
        elif "{dynamic}" in target or "<dynamic>" in target:
            # Even partial dynamic paths can carry credential markers.
            kind = self._credential_kind_for_str(target)
            if not kind:
                explicit = False
                target = "<dynamic>"
        kind = self._credential_kind_for_str(target) or "regular"
        if target.lower().endswith(".env") or ".env" in target.lower():
            kind = "dotenv"
        self.file_reads.append(
            FileReadFact(
                snippet=_src_segment(self.source, node),
                loc=_loc(node),
                target=target,
                kind=kind,
                explicit=explicit,
            ))

    def _collect_path_chain(self, node: ast.AST) -> str:
        """Flatten ``Path(a) / "b" / "c"`` into ``<dynamic>/b/c``."""

        parts: list[str] = []
        self._walk_path_chain(node, parts)
        if not parts:
            return ""
        return "/".join(parts)

    def _walk_path_chain(self, node: ast.AST | None, parts: list[str]) -> None:
        if node is None:
            return
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            self._walk_path_chain(node.left, parts)
            self._walk_path_chain(node.right, parts)
            return
        if isinstance(node, ast.Call):
            canonical = self._canonical_name(node.func)
            if canonical in {"pathlib.Path", "Path", "PosixPath", "pathlib.PurePath", "pathlib.PurePosixPath"}:
                if node.args:
                    arg_text = _const_str(node.args[0]) \
                        or _format_string(node.args[0])
                    parts.append(arg_text or "<dynamic>")
                return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            parts.append(node.value)
            return
        if isinstance(node, ast.Name):
            alias = self._path_aliases.get(node.id)
            if alias is not None:
                self._walk_path_chain(alias, parts)
                return
            parts.append("<dynamic>")
            return
        parts.append("<dynamic>")

    def _credential_kind_for_str(self, target: str) -> str | None:
        if not target:
            return None
        lowered = target.lower()
        if ".ssh" in lowered or "id_rsa" in lowered or "id_ecdsa" in lowered \
                or "id_ed25519" in lowered:
            return "credential"
        if "credentials" in lowered or ".netrc" in lowered \
                or "kubeconfig" in lowered or ".aws/credentials" in lowered:
            return "credential"
        if lowered.endswith(".pem") or lowered.endswith(".key") \
                or lowered.endswith(".p12") or lowered.endswith(".pfx"):
            return "credential"
        return None

    # ----- network ----- #

    def _handle_network_call(self, node: ast.Call, canonical: str) -> bool:
        parts = canonical.split(".")
        if len(parts) >= 2:
            lib = ".".join(parts[:-1])
            method = parts[-1]
        else:
            lib = canonical
            method = ""
        if lib not in _NETWORK_LIBS and canonical not in _NETWORK_LIBS:
            return False
        library_name = lib if lib in _NETWORK_LIBS else canonical
        allowed_methods = _NETWORK_LIBS.get(library_name, ())
        if allowed_methods and method not in allowed_methods and canonical not in _NETWORK_LIBS:
            return False
        target, dynamic = self._extract_network_target(node)
        if target is None and not dynamic:
            # Couldn't find a literal and not flagged dynamic -- still
            # record as dynamic so the call doesn't disappear silently.
            dynamic = True
        self.network_calls.append(
            NetworkFact(
                snippet=_src_segment(self.source, node),
                loc=_loc(node),
                target=target or "",
                library=library_name,
                dynamic=dynamic,
            ))
        # Secret flow: tainted value flows into params/data/headers.
        if self._tainted_flows_into_call(node):
            self.secret_flows.append(
                SecretFlowFact(
                    snippet=_src_segment(self.source, node),
                    loc=_loc(node),
                    source="<tainted>",
                    sink=canonical,
                    sink_kind="network",
                ))
        return True

    def _extract_network_target(self, node: ast.Call) -> tuple[str | None, bool]:
        # Look for the first string-like argument or url= keyword.
        candidates: list[ast.AST] = list(node.args)
        for kw in node.keywords:
            if kw.arg in {"url", "uri", "host", "address", "address_tuple"}:
                candidates.insert(0, kw.value)
        for cand in candidates:
            text = _format_string(cand)
            if text is not None:
                if "{dynamic}" in text:
                    # If the dynamic part is only in the path/query, we
                    # can still extract the host from the prefix.
                    host = _host_from_url(text.replace("{dynamic}", ""))
                    if host and not _looks_dynamic_host(host):
                        return (host, False)
                    return (None, True)
                host = _host_from_url(text)
                if host:
                    return (host, False)
            else:
                return (None, True)
        return (None, True)

    # ----- process / subprocess ----- #

    def _handle_process_call(self, node: ast.Call, canonical: str) -> bool:
        if canonical in {
                "subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call",
                "subprocess.check_output"
        }:
            self._record_subprocess(node, canonical)
            return True
        if canonical in {"os.system", "os.popen"}:
            self._record_system_call(node, canonical, shell=True)
            return True
        if canonical == "pty.spawn":
            self._record_system_call(node, canonical, shell=True)
            return True
        return False

    def _record_subprocess(self, node: ast.Call, canonical: str) -> None:
        shell = False
        for kw in node.keywords:
            if kw.arg == "shell" and isinstance(kw.value, ast.Constant):
                shell = bool(kw.value.value)
        command_str, has_operators, dynamic = self._subprocess_command(node)
        self.process_calls.append(
            ProcessFact(
                snippet=_src_segment(self.source, node),
                loc=_loc(node),
                command=command_str,
                shell=shell if shell else None,
                has_operators=has_operators,
            ))
        # Privilege / dependency inspection
        self._maybe_record_special_command(command_str, node, dynamic)
        if shell:
            self.shell_operators.append(
                ShellOperatorFact(
                    snippet=_src_segment(self.source, node),
                    loc=_loc(node),
                    operator="shell=True",
                ))
        if self._tainted_flows_into_call(node):
            self.secret_flows.append(
                SecretFlowFact(
                    snippet=_src_segment(self.source, node),
                    loc=_loc(node),
                    source="<tainted>",
                    sink=canonical,
                    sink_kind="subprocess",
                ))

    def _record_system_call(self, node: ast.Call, canonical: str, *, shell: bool) -> None:
        command_str, has_operators, dynamic = self._subprocess_command(node)
        self.process_calls.append(
            ProcessFact(
                snippet=_src_segment(self.source, node),
                loc=_loc(node),
                command=command_str,
                shell=shell,
                has_operators=has_operators,
            ))
        self._maybe_record_special_command(command_str, node, dynamic)
        if has_operators:
            for op in _SHELL_OPERATORS_IN(command_str):
                self.shell_operators.append(
                    ShellOperatorFact(
                        snippet=_src_segment(self.source, node),
                        loc=_loc(node),
                        operator=op,
                    ))
        if self._tainted_flows_into_call(node):
            self.secret_flows.append(
                SecretFlowFact(
                    snippet=_src_segment(self.source, node),
                    loc=_loc(node),
                    source="<tainted>",
                    sink=canonical,
                    sink_kind="subprocess",
                ))

    def _subprocess_command(self, node: ast.Call) -> tuple[str, bool, bool]:
        """Return (command_text, has_shell_operators, dynamic)."""

        if not node.args:
            return ("", False, True)
        first = node.args[0]
        # subprocess.run(["ls", "-l"]) -- list form
        if isinstance(first, ast.List):
            tokens = []
            for item in first.elts:
                text = _const_str(item)
                if text is None:
                    return (" ".join(tokens), False, True)
                tokens.append(text)
            return (" ".join(tokens), False, False)
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return (first.value, bool(_SHELL_OPERATORS_IN(first.value)), False)
        # Dynamic construction
        return ("", False, True)

    def _maybe_record_special_command(self, command_str: str, node: ast.Call, dynamic: bool) -> None:
        if dynamic or not command_str:
            return
        tokens = command_str.split()
        if not tokens:
            return
        executable = tokens[0].lower()
        if executable in _PRIVILEGE_COMMANDS:
            self.privilege_commands.append(
                PrivilegeFact(
                    snippet=_src_segment(self.source, node),
                    loc=_loc(node),
                    command=executable,
                ))
        # pip install / npm install / apt install / ...
        if executable in _PACKAGE_MANAGERS:
            install_subcommands = _PACKAGE_MANAGERS[executable]
            if executable == "python" and "-m" in tokens:
                idx = tokens.index("-m") + 1 if "-m" in tokens else -1
                if idx > 0 and idx < len(tokens) \
                        and tokens[idx] in {"pip", "pip3"} \
                        and idx + 1 < len(tokens) \
                        and tokens[idx + 1] == "install":
                    self.dependency_installs.append(
                        DependencyInstallFact(
                            snippet=_src_segment(self.source, node),
                            loc=_loc(node),
                            manager=tokens[idx],
                            command=" ".join(tokens[idx:]),
                        ))
                    return
            if install_subcommands and len(tokens) >= 2 \
                    and tokens[1] in install_subcommands:
                self.dependency_installs.append(
                    DependencyInstallFact(
                        snippet=_src_segment(self.source, node),
                        loc=_loc(node),
                        manager=executable,
                        command=command_str,
                    ))

    # ----- sleep ----- #

    def _handle_sleep(self, node: ast.Call) -> None:
        duration: float | None = None
        raw = ""
        if node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                duration = float(arg.value)
                raw = str(arg.value)
            else:
                raw = _src_segment(self.source, arg)
        self.long_sleeps.append(
            LongSleepFact(
                snippet=_src_segment(self.source, node),
                loc=_loc(node),
                duration_seconds=duration,
                raw=raw,
            ))

    # ----- concurrency ----- #

    def _handle_concurrency_call(self, node: ast.Call, canonical: str) -> bool:
        if canonical in {
                "multiprocessing.Process", "multiprocessing.Pool", "concurrent.futures.ThreadPoolExecutor",
                "concurrent.futures.ProcessPoolExecutor", "threading.Thread"
        }:
            count = self._static_thread_count(node)
            self.concurrency.append(
                ConcurrencyFact(
                    snippet=_src_segment(self.source, node),
                    loc=_loc(node),
                    count=count,
                    raw=canonical,
                ))
            return True
        if canonical == "os.fork":
            self.fork_bombs.append(
                ForkBombFact(
                    snippet=_src_segment(self.source, node),
                    loc=_loc(node),
                    pattern="os.fork",
                ))
            return True
        return False

    def _static_thread_count(self, node: ast.Call) -> int | None:
        for kw in node.keywords:
            if kw.arg in {"max_workers", "processes", "threads", "n"}:
                if isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, int):
                    return kw.value.value
        return None

    # ----- sinks ----- #

    def _handle_sink_call(self, node: ast.Call, canonical: str) -> None:
        sink_kind: str | None = None
        sink_name: str | None = None
        if canonical in {"print", "pprint.pprint", "pprint.pp"}:
            sink_kind = "output"
            sink_name = canonical
        elif canonical.startswith("logging.") or canonical in {
                "logger.info", "logger.debug", "logger.warning", "logger.error", "logger.critical", "logger.log",
                "logger.exception"
        }:
            sink_kind = "output"
            sink_name = canonical
        elif canonical in {
                "open", "io.open", "codecs.open", "pathlib.Path.open", "pathlib.Path.write_text",
                "pathlib.Path.write_bytes"
        }:
            sink_kind = "file"
            sink_name = canonical
        # Detect calls on a local variable named ``logger``/``log`` whose
        # method is a known logging primitive.
        if sink_kind is None and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if method in {"info", "debug", "warning", "warn", "error", "critical", "exception", "log"}:
                if isinstance(node.func.value, ast.Name):
                    if node.func.value.id.lower() in {"log", "logger", "logging"}:
                        sink_kind = "output"
                        sink_name = f"log.{method}"
        if sink_kind is None:
            return
        if not self._tainted_flows_into_call(node):
            return
        # Also check large constant writes to file
        if sink_kind == "file":
            size = self._static_size(node.args[-1] if node.args else None)
            if size is not None and size > 0:
                target = (_const_str(node.args[0]) if node.args else "") or "<dynamic>"
                self.large_writes.append(
                    LargeWriteFact(
                        snippet=_src_segment(self.source, node),
                        loc=_loc(node),
                        size=size,
                        target=target,
                        raw=canonical,
                    ))
        self.secret_flows.append(
            SecretFlowFact(
                snippet=_src_segment(self.source, node),
                loc=_loc(node),
                source="<tainted>",
                sink=sink_name or canonical,
                sink_kind=sink_kind,  # type: ignore[arg-type]
            ))

    # ----- loops ----- #

    def _visit_loop(self, node: ast.AST) -> None:
        if isinstance(node, ast.While):
            test = node.test
            if isinstance(test, ast.Constant) and test.value is True:
                # while True with no break -> unbounded
                if not _has_break(node):
                    self.unbounded_loops.append(
                        UnboundedLoopFact(
                            snippet=_src_segment(self.source, node),
                            loc=_loc(node),
                            kind="while-True",
                        ))
                return
            if isinstance(test, ast.Name) and test.id == "True":
                if not _has_break(node):
                    self.unbounded_loops.append(
                        UnboundedLoopFact(
                            snippet=_src_segment(self.source, node),
                            loc=_loc(node),
                            kind="while-True-name",
                        ))
        elif isinstance(node, ast.For):
            iter_node = node.iter
            if isinstance(iter_node, ast.Call):
                target_name = self._canonical_name(iter_node.func)
                if target_name in {"itertools.cycle", "iter"} \
                        and not _has_break(node):
                    self.unbounded_loops.append(
                        UnboundedLoopFact(
                            snippet=_src_segment(self.source, node),
                            loc=_loc(node),
                            kind=target_name,
                        ))

    # ----- helpers ----- #

    def _canonical_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self._alias_to_canonical.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self._canonical_name(node.value)
            if not base:
                return node.attr
            return f"{base}.{node.attr}"
        return ""

    def _name_of(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        return ""

    def _tainted_flows_into_call(self, node: ast.Call) -> bool:
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if self._expr_tainted(arg):
                return True
        return False

    def _expr_tainted(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return contains_secret_literal(node.value)
        if isinstance(node, ast.Name):
            return node.id in self._tainted
        if isinstance(node, ast.Subscript):
            return self._secret_source_label(node) is not None
        if isinstance(node, ast.JoinedStr):
            return any(self._expr_tainted(v) for v in node.values)
        if isinstance(node, ast.FormattedValue):
            return self._expr_tainted(node.value)
        if isinstance(node, ast.BinOp):
            return self._expr_tainted(node.left) \
                or self._expr_tainted(node.right)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return any(self._expr_tainted(e) for e in node.elts)
        if isinstance(node, ast.Dict):
            return any(self._expr_tainted(v) for v in node.values if v is not None)
        if isinstance(node, ast.Call):
            # open(<tainted>) etc
            return any(self._expr_tainted(a) for a in node.args)
        return False

    def _static_size(self, node: ast.AST | None) -> int | None:
        if node is None:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, (bytes, str)):
            return len(node.value)
        if isinstance(node, ast.JoinedStr):
            total = 0
            for value in node.values:
                size = self._static_size(value)
                if size is None:
                    return None
                total += size
            return total
        if isinstance(node, ast.FormattedValue):
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._static_size(node.left)
            right = self._static_size(node.right)
            if left is None or right is None:
                return None
            return left + right
        return None

    def _facts(self) -> ScriptFacts:
        return ScriptFacts(
            language=ScriptLanguage.PYTHON,
            file_deletes=tuple(self.file_deletes),
            file_writes=tuple(self.file_writes),
            file_reads=tuple(self.file_reads),
            network_calls=tuple(self.network_calls),
            process_calls=tuple(self.process_calls),
            shell_operators=tuple(self.shell_operators),
            privilege_commands=tuple(self.privilege_commands),
            dependency_installs=tuple(self.dependency_installs),
            unbounded_loops=tuple(self.unbounded_loops),
            fork_bombs=tuple(self.fork_bombs),
            long_sleeps=tuple(self.long_sleeps),
            concurrency=tuple(self.concurrency),
            large_writes=tuple(self.large_writes),
            secret_flows=tuple(self.secret_flows),
            dynamic_execs=tuple(self.dynamic_execs),
            parse_errors=tuple(self.parse_errors),
        )


# --------------------------------------------------------------------------- #
# Module-level helpers
# --------------------------------------------------------------------------- #


def _first_arg(call: ast.Call) -> ast.AST | None:
    return call.args[0] if call.args else None


def _has_break(node: ast.AST) -> bool:
    for inner in ast.walk(node):
        if isinstance(inner, ast.Break):
            return True
    return False


def _host_from_url(text: str) -> str:
    if "://" in text:
        text = text.split("://", 1)[1]
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    if "/" in text:
        text = text.split("/", 1)[0]
    if ":" in text:
        text = text.split(":", 1)[0]
    return text.lower().strip().rstrip(".")


def _looks_dynamic_host(host: str) -> bool:
    """Heuristic: True if the host text contains interpolation artifacts."""

    if not host:
        return True
    return any(ch in host for ch in "{}$")


def _SHELL_OPERATORS_IN(text: str) -> list[str]:
    hits: list[str] = []
    for op in ("&&", "||", "|", ";", "`", "$(", "&"):
        if op in text:
            hits.append(op)
    return hits


# --------------------------------------------------------------------------- #
# Rule wrapper
# --------------------------------------------------------------------------- #


class PythonScannerRule(_LanguageScannerRule, SafetyRule):
    """Rule that runs the Python scanner once and evaluates the catalog."""

    rule_id = "python_scanner"

    def __init__(self) -> None:
        super().__init__(ScriptLanguage.PYTHON)

    def _extract(self, request) -> ScriptFacts:  # type: ignore[override]
        scanner = _PythonScanner(request.script)
        scanner.parse()
        return scanner.scan()


def scan_python(source: str) -> ScriptFacts:
    """Public entry for tests that want raw facts."""

    scanner = _PythonScanner(source)
    scanner.parse()
    return scanner.scan()
