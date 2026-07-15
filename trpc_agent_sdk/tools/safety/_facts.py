"""Internal fact model produced by language scanners.

Facts are the shared vocabulary between scanners and rules. Scanners
extract them once per script; rules consume them without re-parsing the
source. This keeps the 500-line performance budget intact.

Facts are intentionally permissive about ``None``: when a scanner cannot
determine a value statically (for example, a sleep duration computed at
runtime) it leaves the field ``None`` and rules convert the uncertainty
into ``needs_human_review``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from trpc_agent_sdk.tools.safety._models import ScriptLanguage


@dataclass(frozen=True)
class Loc:
    """Source location (1-based line and column)."""

    line: int = 0
    column: int = 0

    def label(self) -> str:
        if self.line <= 0:
            return ""
        if self.column <= 0:
            return f"L{self.line}"
        return f"L{self.line}:C{self.column}"


@dataclass(frozen=True)
class Fact:
    """Base fact. Carries a snippet and a source location."""

    snippet: str = ""
    loc: Loc = field(default_factory=Loc)


@dataclass(frozen=True)
class FileDeleteFact(Fact):
    target: str = ""
    recursive: bool = False
    explicit: bool = True  # False when target computed at runtime


@dataclass(frozen=True)
class FileWriteFact(Fact):
    target: str = ""
    mode: str = "w"
    explicit: bool = True


@dataclass(frozen=True)
class FileReadFact(Fact):
    target: str = ""
    kind: Literal["credential", "dotenv", "regular"] = "regular"
    explicit: bool = True


@dataclass(frozen=True)
class NetworkFact(Fact):
    target: str = ""
    library: str = ""
    dynamic: bool = False


@dataclass(frozen=True)
class ProcessFact(Fact):
    command: str = ""
    shell: bool | None = None
    has_operators: bool = False


@dataclass(frozen=True)
class ShellOperatorFact(Fact):
    operator: str = ""


@dataclass(frozen=True)
class PrivilegeFact(Fact):
    command: str = ""


@dataclass(frozen=True)
class DependencyInstallFact(Fact):
    manager: str = ""
    command: str = ""


@dataclass(frozen=True)
class UnboundedLoopFact(Fact):
    kind: str = ""


@dataclass(frozen=True)
class ForkBombFact(Fact):
    pattern: str = ""


@dataclass(frozen=True)
class LongSleepFact(Fact):
    duration_seconds: float | None = None
    raw: str = ""


@dataclass(frozen=True)
class ConcurrencyFact(Fact):
    count: int | None = None
    raw: str = ""


@dataclass(frozen=True)
class LargeWriteFact(Fact):
    size: int | None = None
    target: str = ""
    raw: str = ""


@dataclass(frozen=True)
class SecretFlowFact(Fact):
    """Source-to-sink flow of a secret-looking value."""

    source: str = ""
    sink: str = ""
    sink_kind: Literal["output", "file", "network", "subprocess",
                       "unknown"] = "unknown"


@dataclass(frozen=True)
class DynamicExecFact(Fact):
    kind: str = ""  # eval, exec, importlib, getattr, base64-decode-then-exec


@dataclass(frozen=True)
class ParseErrorFact(Fact):
    message: str = ""


@dataclass(frozen=True)
class ScriptFacts:
    """Aggregated facts for one script.

    Lists are deliberately tuples so the structure is immutable and cheap
    to copy.
    """

    language: ScriptLanguage = ScriptLanguage.UNKNOWN
    file_deletes: tuple[FileDeleteFact, ...] = ()
    file_writes: tuple[FileWriteFact, ...] = ()
    file_reads: tuple[FileReadFact, ...] = ()
    network_calls: tuple[NetworkFact, ...] = ()
    process_calls: tuple[ProcessFact, ...] = ()
    shell_operators: tuple[ShellOperatorFact, ...] = ()
    privilege_commands: tuple[PrivilegeFact, ...] = ()
    dependency_installs: tuple[DependencyInstallFact, ...] = ()
    unbounded_loops: tuple[UnboundedLoopFact, ...] = ()
    fork_bombs: tuple[ForkBombFact, ...] = ()
    long_sleeps: tuple[LongSleepFact, ...] = ()
    concurrency: tuple[ConcurrencyFact, ...] = ()
    large_writes: tuple[LargeWriteFact, ...] = ()
    secret_flows: tuple[SecretFlowFact, ...] = ()
    dynamic_execs: tuple[DynamicExecFact, ...] = ()
    parse_errors: tuple[ParseErrorFact, ...] = ()

    def merge(self, other: ScriptFacts) -> ScriptFacts:
        """Merge another fact bag into a new one (immutable)."""

        def _merge(left: tuple, right: tuple) -> tuple:
            return tuple(list(left) + list(right))

        return ScriptFacts(
            language=self.language if self.language != ScriptLanguage.UNKNOWN else other.language,
            file_deletes=_merge(self.file_deletes, other.file_deletes),
            file_writes=_merge(self.file_writes, other.file_writes),
            file_reads=_merge(self.file_reads, other.file_reads),
            network_calls=_merge(self.network_calls, other.network_calls),
            process_calls=_merge(self.process_calls, other.process_calls),
            shell_operators=_merge(self.shell_operators, other.shell_operators),
            privilege_commands=_merge(self.privilege_commands, other.privilege_commands),
            dependency_installs=_merge(self.dependency_installs, other.dependency_installs),
            unbounded_loops=_merge(self.unbounded_loops, other.unbounded_loops),
            fork_bombs=_merge(self.fork_bombs, other.fork_bombs),
            long_sleeps=_merge(self.long_sleeps, other.long_sleeps),
            concurrency=_merge(self.concurrency, other.concurrency),
            large_writes=_merge(self.large_writes, other.large_writes),
            secret_flows=_merge(self.secret_flows, other.secret_flows),
            dynamic_execs=_merge(self.dynamic_execs, other.dynamic_execs),
            parse_errors=_merge(self.parse_errors, other.parse_errors),
        )

    def has_any(self) -> bool:
        return any(
            (
                self.file_deletes, self.file_writes, self.file_reads,
                self.network_calls, self.process_calls, self.shell_operators,
                self.privilege_commands, self.dependency_installs,
                self.unbounded_loops, self.fork_bombs, self.long_sleeps,
                self.concurrency, self.large_writes, self.secret_flows,
                self.dynamic_execs, self.parse_errors,
            )
        )
