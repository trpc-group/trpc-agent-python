"""Conservative Bash lexer that extracts ScriptFacts.

This is intentionally a *lexer-lite*: it tracks quote state and source
offsets, splits commands at the usual shell separators, and inspects
each command's tokens for patterns we care about. It deliberately does
not run the shell, expand variables, or follow redirects.

Anything the lexer cannot understand (unbalanced quotes, unsupported
substitution form) becomes a ``PARSE001_UNCERTAIN`` finding so the
uncertainty is visible rather than silently treated as safe.
"""

from __future__ import annotations

import re

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

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

_NETWORK_COMMANDS = {"curl", "wget", "nc", "ncat", "ssh", "scp", "sftp", "telnet", "ftp", "ncat"}
# Commands where the first non-option arg is always the remote target.
# Used to apply a fail-closed dynamic NetworkFact when parsing fails.
_SSH_FAMILY_COMMANDS = {"ssh", "scp", "sftp", "telnet", "ftp"}
_FILE_READ_COMMANDS = {
    "cat", "head", "tail", "less", "more", "view", "grep", "egrep", "fgrep", "rg", "ack", "sed", "awk", "xxd", "od",
    "cut", "sort"
}
_FILE_WRITE_COMMANDS = {"tee", "dd", "install", "cp", "mv"}
_PRIVILEGE_COMMANDS = {"sudo", "su", "doas", "pkexec", "super"}
_PACKAGE_MANAGERS = {
    "pip": ("install", ),
    "pip3": ("install", ),
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
_INTERPRETERS = {"python", "python3", "python2", "bash", "sh", "zsh", "perl", "ruby", "node"}

_SEPARATORS = ("&&", "||", "|", ";", "\n")
_REDIRECTION_RE = re.compile(r"(?:>>|>|<|<<|<<<|<>|&>|\d?>|\d<)\s*(\S+)")
_FORK_BOMB_RE = re.compile(
    r":\(\)\s*\{\s*:\s*[|&]+\s*:\s*&?\s*\}\s*;?\s*:?",
    re.MULTILINE,
)
_URL_RE = re.compile(r"\bhttps?://([^/\s:]+)", re.IGNORECASE)
_WHILE_TRUE_RE = re.compile(r"\bwhile\s+(?:true|:|\[\s*\[\s*1\s*\]\s*\]\s*)", re.IGNORECASE)
_FOR_INF_RE = re.compile(r"\bfor\s*\(\(\s*;;\s*\)\)", re.IGNORECASE)
_SLEEP_RE = re.compile(r"\bsleep\s+([0-9]+[smhd]?)", re.IGNORECASE)
_BG_COUNT_RE = re.compile(r"&")

# --------------------------------------------------------------------------- #
# Tokenizer
# --------------------------------------------------------------------------- #


class _Token:
    __slots__ = ("text", "line", "col", "was_quoted")

    def __init__(self, text: str, *, line: int, col: int, was_quoted: bool = False) -> None:
        self.text = text
        self.line = line
        self.col = col
        self.was_quoted = was_quoted

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Token({self.text!r}@{self.line}:{self.col})"


class _BashLexer:
    """Split a script into (separator, [tokens]) command groups.

    The lexer preserves quote state so that ``'; rm -rf /'`` stays inside
    a quoted argument and is not mistaken for a separator.
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.position = 0
        self.line = 1
        self.col = 1

    def tokenize(self) -> tuple[list[tuple[str, int, int, list[_Token]]], list[ParseErrorFact]]:
        """Return (commands, errors).

        Each command is a tuple of ``(separator_preceding, line, col,
        tokens)``. The separator helps callers know whether the command
        was chained by ``&&``, ``|``, etc.
        """

        commands: list[tuple[str, int, int, list[_Token]]] = []
        errors: list[ParseErrorFact] = []
        current: list[_Token] = []
        current_op = ""
        current_line = 1
        current_col = 1
        started = False

        while self.position < len(self.source):
            ch = self.source[self.position]
            if ch == "#":
                # Comment to end of line. A leading '#' outside quotes is
                # always a comment in shell grammar.
                while self.position < len(self.source) \
                        and self.source[self.position] != "\n":
                    self._advance()
                continue
            if ch == "\n":
                self._advance()
                if current:
                    commands.append((current_op, current_line, current_col, current))
                    current = []
                current_op = "\n"
                current_line = self.line
                current_col = self.col
                started = False
                continue
            if ch in " \t":
                self._advance()
                continue
            # Check for separator (but only when not quoted)
            for sep in ("&&", "||", "|", ";", "&"):
                if self.source.startswith(sep, self.position):
                    if current:
                        commands.append((current_op, current_line, current_col, current))
                        current = []
                    for _ in sep:
                        self._advance()
                    current_op = sep
                    current_line = self.line
                    current_col = self.col
                    started = False
                    break
            else:
                # Read a token
                if not started:
                    current_line = self.line
                    current_col = self.col
                    started = True
                token_text, quoted, err = self._read_token()
                if err:
                    errors.append(err)
                if token_text:
                    current.append(_Token(token_text, line=self.line, col=self.col, was_quoted=quoted))
                continue
        if current:
            commands.append((current_op, current_line, current_col, current))
        return commands, errors

    def _advance(self) -> None:
        ch = self.source[self.position]
        self.position += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1

    def _read_token(self) -> tuple[str, bool, ParseErrorFact | None]:
        """Read one shell token.

        Returns (text, was_quoted, error). Sets ``was_quoted`` to True if
        the token had any quote/backtick layer; this lets callers skip
        separator detection inside already-consumed text.
        """

        buf: list[str] = []
        quoted = False
        quote_char = ""
        err: ParseErrorFact | None = None
        start_line = self.line
        start_col = self.col
        while self.position < len(self.source):
            ch = self.source[self.position]
            if not quote_char:
                if ch in " \t\n":
                    break
                if ch in ("&", "|", ";"):
                    break
                if ch in ("'", '"', "`"):
                    quote_char = ch
                    quoted = True
                    self._advance()
                    continue
                if ch == "\\":
                    self._advance()
                    if self.position < len(self.source):
                        nxt = self.source[self.position]
                        buf.append(nxt)
                        self._advance()
                    continue
                if ch == "$" and self.position + 1 < len(self.source) \
                        and self.source[self.position + 1] == "(":
                    # $( ... ) command substitution
                    quoted = True
                    buf.append("$(")
                    self._advance()
                    self._advance()
                    depth = 1
                    while self.position < len(self.source) and depth > 0:
                        cur = self.source[self.position]
                        if cur == "(":
                            depth += 1
                        elif cur == ")":
                            depth -= 1
                        buf.append(cur)
                        self._advance()
                    continue
                buf.append(ch)
                self._advance()
            else:
                if ch == quote_char:
                    quote_char = ""
                    self._advance()
                    continue
                if quote_char == "'" or quote_char == "`":
                    buf.append(ch)
                    self._advance()
                else:
                    if ch == "\\" and self.position + 1 < len(self.source):
                        nxt = self.source[self.position + 1]
                        buf.append(nxt)
                        self._advance()
                        self._advance()
                        continue
                    buf.append(ch)
                    self._advance()
        if quote_char:
            err = ParseErrorFact(
                snippet=f"unbalanced {quote_char}",
                loc=Loc(line=start_line, column=start_col),
                message=f"unbalanced quote {quote_char!r}",
            )
        return ("".join(buf), quoted, err)


# --------------------------------------------------------------------------- #
# Scanner
# --------------------------------------------------------------------------- #


class _BashScanner:

    def __init__(self, source: str) -> None:
        self.source = source
        self.lexer = _BashLexer(source)
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

    def scan(self) -> ScriptFacts:
        commands, errors = self.lexer.tokenize()
        # Fork-bomb detection runs over the whole source.
        for match in _FORK_BOMB_RE.finditer(self.source):
            self.fork_bombs.append(
                ForkBombFact(
                    snippet=match.group(0),
                    loc=Loc(line=_line_of(self.source, match.start()), column=_col_of(self.source, match.start())),
                    pattern="classic_bomb",
                ))
        for match in _WHILE_TRUE_RE.finditer(self.source):
            self.unbounded_loops.append(
                UnboundedLoopFact(
                    snippet=match.group(0),
                    loc=Loc(line=_line_of(self.source, match.start()), column=_col_of(self.source, match.start())),
                    kind="while-true",
                ))
        for match in _FOR_INF_RE.finditer(self.source):
            self.unbounded_loops.append(
                UnboundedLoopFact(
                    snippet=match.group(0),
                    loc=Loc(line=_line_of(self.source, match.start()), column=_col_of(self.source, match.start())),
                    kind="for-infinite",
                ))
        for match in _SLEEP_RE.finditer(self.source):
            raw = match.group(1)
            duration = _parse_sleep(raw)
            self.long_sleeps.append(
                LongSleepFact(
                    snippet=match.group(0),
                    loc=Loc(line=_line_of(self.source, match.start()), column=_col_of(self.source, match.start())),
                    duration_seconds=duration,
                    raw=raw,
                ))
        # Background count
        bg_count = self.source.count("&") - self.source.count("&&")
        # Heuristic: many & in script -> high concurrency
        if bg_count >= 8:
            self.concurrency.append(
                ConcurrencyFact(
                    snippet=f"<{bg_count} background jobs>",
                    loc=Loc(),
                    count=bg_count,
                    raw="background-jobs",
                ))

        for op, line, col, tokens in commands:
            if op in ("&&", "||", "|", "&"):
                self.shell_operators.append(ShellOperatorFact(
                    snippet=op,
                    loc=Loc(line=line, column=col),
                    operator=op,
                ))
            if not tokens:
                continue
            self._handle_command(op, line, col, tokens)

        return ScriptFacts(
            language=ScriptLanguage.BASH,
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
            parse_errors=tuple(errors),
        )

    # ----- per-command ----- #

    def _handle_command(self, preceding_op: str, line: int, col: int, tokens: list[_Token]) -> None:
        executable = tokens[0].text
        argv = [t.text for t in tokens[1:]]
        snippet = self._segment_for(line, col)
        loc = Loc(line=line, column=col)

        # Strip leading env var assignments (FOO=bar cmd ...)
        while "=" in executable and not executable.startswith("-"):
            if not _looks_like_env_assignment(executable):
                break
            if len(tokens) < 2:
                return
            tokens = tokens[1:]
            executable = tokens[0].text
            argv = [t.text for t in tokens[1:]]

        exec_lower = executable.lower()

        # Privilege
        if exec_lower in _PRIVILEGE_COMMANDS:
            self.privilege_commands.append(PrivilegeFact(
                snippet=snippet,
                loc=loc,
                command=exec_lower,
            ))
            if tokens[1:]:
                executable = tokens[1].text
                exec_lower = executable.lower()
                argv = [t.text for t in tokens[2:]]

        # rm with -rf/-fr
        if exec_lower == "rm":
            self._handle_rm(argv, snippet, loc)
            self.process_calls.append(
                ProcessFact(
                    snippet=snippet,
                    loc=loc,
                    command=executable,
                    shell=None,
                    has_operators=False,
                ))
            return

        # curl/wget/nc/ssh
        if exec_lower in _NETWORK_COMMANDS:
            self._handle_network(argv, exec_lower, snippet, loc)
            self.process_calls.append(
                ProcessFact(
                    snippet=snippet,
                    loc=loc,
                    command=executable,
                    shell=None,
                    has_operators=preceding_op in ("|", "&", "&&", "||"),
                ))
            return

        # File reads
        if exec_lower in _FILE_READ_COMMANDS:
            self._handle_file_read(argv, exec_lower, snippet, loc)
            self.process_calls.append(
                ProcessFact(
                    snippet=snippet,
                    loc=loc,
                    command=executable,
                    shell=None,
                    has_operators=preceding_op in ("|", "&", "&&", "||"),
                ))
            return

        # File writes
        if exec_lower in _FILE_WRITE_COMMANDS:
            self._handle_file_write(argv, exec_lower, snippet, loc)

        # Package managers
        if exec_lower in _PACKAGE_MANAGERS:
            install_sub = _PACKAGE_MANAGERS[exec_lower]
            if argv and argv[0] in install_sub:
                self.dependency_installs.append(
                    DependencyInstallFact(
                        snippet=snippet,
                        loc=loc,
                        manager=exec_lower,
                        command=" ".join([executable] + argv),
                    ))
            self.process_calls.append(
                ProcessFact(
                    snippet=snippet,
                    loc=loc,
                    command=executable,
                    shell=None,
                    has_operators=preceding_op in ("|", "&", "&&", "||"),
                ))
            return

        # These constructs make a second program or command stream execute
        # outside the script currently being scanned. Do not claim that the
        # outer command is safe merely because its executable is allowlisted.
        if exec_lower in {"source", "."}:
            self.dynamic_execs.append(DynamicExecFact(
                snippet=snippet,
                loc=loc,
                kind="source-file",
            ))
            return
        if exec_lower == "xargs":
            self.dynamic_execs.append(DynamicExecFact(
                snippet=snippet,
                loc=loc,
                kind="xargs-command-stream",
            ))
            return
        if exec_lower == "find" and any(arg in {"-exec", "-execdir", "-ok", "-okdir"} for arg in argv):
            self.dynamic_execs.append(DynamicExecFact(
                snippet=snippet,
                loc=loc,
                kind="find-exec",
            ))
            return

        # eval / bash -c / sh -c
        if exec_lower == "eval":
            self.dynamic_execs.append(DynamicExecFact(
                snippet=snippet,
                loc=loc,
                kind="eval",
            ))
            return
        if exec_lower in ("bash", "sh", "zsh") and argv and argv[0] == "-c":
            self.dynamic_execs.append(DynamicExecFact(
                snippet=snippet,
                loc=loc,
                kind=f"{exec_lower}-c",
            ))
            if len(argv) > 1:
                self.shell_operators.append(
                    ShellOperatorFact(
                        snippet=argv[1][:40],
                        loc=loc,
                        operator=f"{exec_lower} -c",
                    ))
            return
        if _is_python_pip_install(exec_lower, argv):
            self.dependency_installs.append(
                DependencyInstallFact(
                    snippet=snippet,
                    loc=loc,
                    manager=argv[1],
                    command=" ".join([executable] + argv),
                ))
            self.process_calls.append(
                ProcessFact(
                    snippet=snippet,
                    loc=loc,
                    command=executable,
                    shell=None,
                    has_operators=preceding_op in ("|", "&", "&&", "||"),
                ))
            return
        if exec_lower in _INTERPRETERS and _interpreter_runs_payload(argv):
            self.dynamic_execs.append(DynamicExecFact(
                snippet=snippet,
                loc=loc,
                kind=f"{exec_lower}-payload",
            ))
            return

        # sleep
        if exec_lower == "sleep":
            raw = argv[0] if argv else ""
            duration = _parse_sleep(raw)
            self.long_sleeps.append(LongSleepFact(
                snippet=snippet,
                loc=loc,
                duration_seconds=duration,
                raw=raw,
            ))
            return

        # dd/fallocate large writes
        if exec_lower in {"dd", "fallocate", "truncate"}:
            size = _extract_dd_size(argv)
            target = _extract_dd_target(argv)
            self.large_writes.append(
                LargeWriteFact(
                    snippet=snippet,
                    loc=loc,
                    size=size,
                    target=target,
                    raw=executable,
                ))
            self.process_calls.append(
                ProcessFact(
                    snippet=snippet,
                    loc=loc,
                    command=executable,
                    shell=None,
                    has_operators=preceding_op in ("|", "&", "&&", "||"),
                ))
            return

        # echo / printf with secret env reference
        if exec_lower in {"echo", "printf"}:
            for arg in argv:
                if _looks_like_secret_ref(arg):
                    self.secret_flows.append(
                        SecretFlowFact(
                            snippet=snippet,
                            loc=loc,
                            source=arg,
                            sink=exec_lower,
                            sink_kind="output",
                        ))
            self.process_calls.append(
                ProcessFact(
                    snippet=snippet,
                    loc=loc,
                    command=executable,
                    shell=None,
                    has_operators=preceding_op in ("|", "&", "&&", "||"),
                ))
            return

        # Generic process call
        self.process_calls.append(
            ProcessFact(
                snippet=snippet,
                loc=loc,
                command=executable,
                shell=None,
                has_operators=preceding_op in ("|", "&", "&&", "||"),
            ))

        # Check for URL in any argument (catch-all for http(s)://)
        for arg in argv:
            for match in _URL_RE.finditer(arg):
                host = match.group(1)
                self.network_calls.append(
                    NetworkFact(
                        snippet=snippet,
                        loc=loc,
                        target=host,
                        library=exec_lower,
                        dynamic=False,
                    ))

        # Inspect source line for redirection targets
        for match in _REDIRECTION_RE.finditer(self._source_line(line)):
            target = match.group(1).strip()
            if target and not target.startswith("&"):
                self.file_writes.append(
                    FileWriteFact(
                        snippet=snippet,
                        loc=loc,
                        target=target,
                        mode="w",
                        explicit=True,
                    ))

    def _handle_rm(self, argv: list[str], snippet: str, loc: Loc) -> None:
        recursive = False
        explicit_target = ""
        for arg in argv:
            if arg in ("-r", "-R", "--recursive", "-rf", "-fr", "-Rf", "-rF", "-rfv", "-frv"):
                recursive = True
                continue
            if arg.startswith("-"):
                continue
            explicit_target = arg
        self.file_deletes.append(
            FileDeleteFact(
                snippet=snippet,
                loc=loc,
                target=explicit_target or "<unknown>",
                recursive=recursive,
                explicit=bool(explicit_target),
            ))

    def _handle_network(self, argv: list[str], command: str, snippet: str, loc: Loc) -> None:
        # scp/rsync-style commands put the remote target LAST (after any
        # local file args). Iterate in reverse so user@host wins over a
        # local filename that happens to match the plain-host regex.
        if command in {"scp", "rsync"}:
            iteration = list(reversed(argv))
        else:
            iteration = list(argv)
        for arg in iteration:
            if arg.startswith("-"):
                continue
            match = _URL_RE.match(arg) if "://" in arg else None
            if match:
                host = match.group(1)
                self.network_calls.append(
                    NetworkFact(
                        snippet=snippet,
                        loc=loc,
                        target=host,
                        library=command,
                        dynamic=False,
                    ))
                return
            # ssh/scp/sftp user@host[:path] form
            user_host = _extract_user_at_host(arg)
            if user_host:
                self.network_calls.append(
                    NetworkFact(
                        snippet=snippet,
                        loc=loc,
                        target=user_host,
                        library=command,
                        dynamic=False,
                    ))
                return
            # Plain hostname argument (curl example.com)
            if _looks_like_host(arg):
                self.network_calls.append(
                    NetworkFact(
                        snippet=snippet,
                        loc=loc,
                        target=arg.lower(),
                        library=command,
                        dynamic=False,
                    ))
                return
            # Dynamic
            if arg.startswith("$") or "$(" in arg or "`" in arg:
                self.network_calls.append(
                    NetworkFact(
                        snippet=snippet,
                        loc=loc,
                        target="",
                        library=command,
                        dynamic=True,
                    ))
                return
        # Fail-closed for ssh family: if we walked every non-option arg
        # without recognizing a target, emit a dynamic NetworkFact so
        # NET002_DYNAMIC_TARGET surfaces for review instead of silently
        # allowing a bypass.
        if command in _SSH_FAMILY_COMMANDS and not self.network_calls:
            self.network_calls.append(NetworkFact(
                snippet=snippet,
                loc=loc,
                target="",
                library=command,
                dynamic=True,
            ))

    def _handle_file_read(self, argv: list[str], command: str, snippet: str, loc: Loc) -> None:
        for arg in argv:
            if arg.startswith("-"):
                continue
            kind = "regular"
            lowered = arg.lower()
            if ".ssh" in lowered or "id_rsa" in lowered \
                    or "id_ecdsa" in lowered or "id_ed25519" in lowered \
                    or "credentials" in lowered or ".netrc" in lowered \
                    or "kubeconfig" in lowered or ".aws/credentials" in lowered \
                    or lowered.endswith(".pem") or lowered.endswith(".key") \
                    or lowered.endswith(".p12"):
                kind = "credential"
            if lowered.endswith(".env") or ".env" in lowered:
                kind = "dotenv"
            if kind != "regular":
                self.file_reads.append(FileReadFact(
                    snippet=snippet,
                    loc=loc,
                    target=arg,
                    kind=kind,
                    explicit=True,
                ))

    def _handle_file_write(self, argv: list[str], command: str, snippet: str, loc: Loc) -> None:
        # tee / dd / cp / mv / install
        targets: list[str] = []
        if command == "tee":
            for arg in argv:
                if arg.startswith("-"):
                    continue
                targets.append(arg)
        elif command in {"cp", "mv", "install"}:
            if len(argv) >= 2:
                targets.append(argv[-1])
        elif command == "dd":
            target = _extract_dd_target(argv)
            if target:
                targets.append(target)
        for target in targets:
            self.file_writes.append(FileWriteFact(
                snippet=snippet,
                loc=loc,
                target=target,
                mode="w",
                explicit=True,
            ))

    def _segment_for(self, line: int, col: int) -> str:
        # Cheap approximation: return the source line.
        return self._source_line(line)

    def _source_line(self, line: int) -> str:
        if line <= 0:
            return ""
        try:
            return self.source.splitlines()[line - 1]
        except IndexError:
            return ""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _col_of(source: str, offset: int) -> int:
    last_nl = source.rfind("\n", 0, offset)
    return offset - last_nl


def _parse_sleep(raw: str) -> float | None:
    if not raw:
        return None
    if raw.isdigit():
        return float(raw)
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if len(raw) >= 2 and raw[-1].lower() in mult and raw[:-1].isdigit():
        return float(raw[:-1]) * mult[raw[-1].lower()]
    return None


def _is_python_pip_install(executable: str, argv: list[str]) -> bool:
    if executable not in {"python", "python2", "python3"}:
        return False
    try:
        module_index = argv.index("-m")
    except ValueError:
        return False
    return module_index + 2 < len(argv) \
        and argv[module_index + 1] in {"pip", "pip3"} \
        and argv[module_index + 2] == "install"


def _interpreter_runs_payload(argv: list[str]) -> bool:
    """Return whether an interpreter is being asked to run external code."""

    for arg in argv:
        if arg in {"-c", "-m"}:
            return True
        if arg.startswith("-"):
            continue
        return True
    return False


def _extract_dd_size(argv: list[str]) -> int | None:
    # bs=SIZE count=N
    bs: int | None = None
    count: int | None = None
    for arg in argv:
        if arg.startswith("bs="):
            bs = _parse_dd_size_value(arg[3:])
        elif arg.startswith("count="):
            try:
                count = int(arg[6:])
            except ValueError:
                count = None
    if bs is not None and count is not None:
        return bs * count
    return None


def _parse_dd_size_value(text: str) -> int | None:
    text = text.strip()
    if text.isdigit():
        return int(text)
    mult = {
        "c": 1,
        "w": 2,
        "b": 512,
        "kB": 1000,
        "K": 1024,
        "MB": 1000 * 1000,
        "M": 1024 * 1024,
        "GB": 10**9,
        "G": 1024 * 1024 * 1024
    }
    for suffix, value in mult.items():
        if text.endswith(suffix):
            head = text[:-len(suffix)]
            if head.isdigit():
                return int(head) * value
    return None


def _extract_dd_target(argv: list[str]) -> str:
    for arg in argv:
        if arg.startswith("of="):
            return arg[3:]
    return ""


def _looks_like_env_assignment(text: str) -> bool:
    if "=" not in text:
        return False
    name = text.split("=", 1)[0]
    return bool(name) and name.replace("_", "").isalnum() and \
        not name.startswith("-") and not name.startswith("(")


def _looks_like_host(text: str) -> bool:
    if not text:
        return False
    if text.startswith("-"):
        return False
    return bool(re.match(r"^[A-Za-z0-9_.-]+(?:\.[A-Za-z0-9_.-]+)+$", text))


def _extract_user_at_host(text: str) -> str | None:
    """Extract a host from ssh-style ``user@host[:path]`` arguments.

    Returns the lowercased host when the part after ``@`` looks like a
    valid host, otherwise ``None``. This closes a bypass where
    ``ssh user@evil.example.com`` produced no NetworkFact because the
    ``@`` character fails the plain host regex.
    """

    if not text or "@" not in text:
        return None
    _, _, after_at = text.partition("@")
    # scp/sftp forms append :port or :path after the host.
    candidate = after_at.split(":", 1)[0]
    if _looks_like_host(candidate):
        return candidate.lower()
    return None


def _looks_like_secret_ref(text: str) -> bool:
    return bool(re.search(r"\$\{?[A-Za-z_][A-Za-z0-9_]*"
                          r"(KEY|TOKEN|PASSWORD|SECRET|CREDENTIAL)", text, re.IGNORECASE))


# --------------------------------------------------------------------------- #
# Rule
# --------------------------------------------------------------------------- #


class BashScannerRule(_LanguageScannerRule, SafetyRule):
    """Rule that runs the Bash scanner once and evaluates the catalog."""

    rule_id = "bash_scanner"

    def __init__(self) -> None:
        super().__init__(ScriptLanguage.BASH)

    def _extract(self, request) -> ScriptFacts:  # type: ignore[override]
        return _BashScanner(request.script).scan()


def scan_bash(source: str) -> ScriptFacts:
    """Public entry for tests that want raw facts."""

    return _BashScanner(source).scan()
