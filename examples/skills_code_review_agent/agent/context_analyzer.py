"""Reconstruct enough post-image context from a diff to reason about findings.

A unified diff is not a program, so the rules that run over single added lines
cannot see whether a handle is closed three lines later or whether an f-string
actually interpolates anything. This module recovers the best available context
for each changed file and exposes it in post-image line coordinates:

- For a newly added file the diff contains every line, so the whole file parses.
- For a modified file each hunk is reparsed on its own. Removed lines are absent
  from the post-image, so the surviving ``+`` and context lines are contiguous
  and map linearly onto ``hunk.new_start``.
- When a fragment is not valid Python on its own -- it usually starts midway
  through a class or function body -- it is dedented and, failing that, wrapped
  in a synthetic function so the body still parses.

Whatever tier succeeded is recorded on the finding, so a report shows whether a
suppression rested on real AST evidence or on a textual window.
"""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Iterator
from dataclasses import dataclass, field

from .models import ChangedFile


@dataclass
class Fragment:
    """One parsed region of a file, in post-image line coordinates."""

    tree: ast.AST
    line_offset: int
    start: int
    end: int
    tier: str

    def line_of(self, node: ast.AST) -> int:
        """Map an AST node back to its post-image line number."""
        return int(getattr(node, "lineno", 0)) + self.line_offset

    def covers(self, line: int) -> bool:
        return self.start <= line <= self.end


@dataclass
class FileContext:
    """Recovered context for one changed file."""

    path: str
    is_new: bool
    fragments: list[Fragment] = field(default_factory=list)
    source_lines: dict[int, str] = field(default_factory=dict)
    windows: dict[int, list[str]] = field(default_factory=dict)

    @property
    def tier(self) -> str:
        """Return the strongest evidence tier available for this file."""
        return "ast" if self.fragments else "window"

    def fragment_for(self, line: int) -> Fragment | None:
        for fragment in self.fragments:
            if fragment.covers(line):
                return fragment
        return None

    def window(self, line: int) -> list[str]:
        """Return the hunk lines surrounding a post-image line."""
        return self.windows.get(line, [])

    def nodes_at(self, line: int) -> Iterator[tuple[ast.AST, Fragment]]:
        """Yield AST nodes whose own line maps to the given post-image line."""
        fragment = self.fragment_for(line)
        if fragment is None:
            return
        for node in ast.walk(fragment.tree):
            if getattr(node, "lineno", None) is not None and fragment.line_of(node) == line:
                yield node, fragment

    def enclosing_scope(self, line: int) -> ast.AST | None:
        """Return the innermost function or module containing a post-image line."""
        fragment = self.fragment_for(line)
        if fragment is None:
            return None
        best: ast.AST | None = fragment.tree
        best_span = 10**9
        for node in ast.walk(fragment.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            start = fragment.line_of(node)
            end = int(getattr(node, "end_lineno", 0)) + fragment.line_offset
            if start <= line <= end and (end - start) < best_span:
                best, best_span = node, end - start
        return best


def build_file_contexts(changed_files: list[ChangedFile]) -> dict[str, FileContext]:
    """Build a context object per changed file, keyed by path."""
    contexts: dict[str, FileContext] = {}
    for changed_file in changed_files:
        contexts[changed_file.path] = _build_one(changed_file)
    return contexts


def _build_one(changed_file: ChangedFile) -> FileContext:
    context = FileContext(path=changed_file.path, is_new=changed_file.is_new)

    for hunk in changed_file.hunks:
        post_lines: list[str] = []
        numbers: list[int] = []
        for line in hunk.lines:
            if line.kind == "-":
                continue
            post_lines.append(line.content)
            numbers.append(line.new_line or 0)

        if not post_lines:
            continue

        window = list(post_lines)
        for number, content in zip(numbers, post_lines):
            if number:
                context.source_lines[number] = content
                context.windows[number] = window

        if not _is_python(changed_file.path):
            continue

        start = numbers[0] or hunk.new_start
        parsed = _parse_fragment(post_lines, base_line=start)
        if parsed is not None:
            tree, offset, tier = parsed
            context.fragments.append(
                Fragment(tree=tree, line_offset=offset, start=start,
                         end=start + len(post_lines) - 1, tier=tier))

    return context


def _parse_fragment(lines: list[str], *, base_line: int) -> tuple[ast.AST, int, str] | None:
    """Parse a hunk fragment, returning the tree, line offset and evidence tier."""
    body = "\n".join(lines)

    # Tier 1: the fragment is already a valid module.
    tree = _try_parse(body)
    if tree is not None:
        return tree, base_line - 1, "ast"

    # Tier 2: the fragment is a uniformly indented block, e.g. a method body.
    dedented = textwrap.dedent(body)
    tree = _try_parse(dedented)
    if tree is not None:
        return tree, base_line - 1, "ast"

    # Tier 3: the fragment is a partial body; give it a synthetic scope. The
    # wrapper adds one line, so the offset shifts by one more.
    wrapped = "def __fragment__():\n" + textwrap.indent(dedented, "    ")
    tree = _try_parse(wrapped)
    if tree is not None:
        return tree, base_line - 2, "ast-wrapped"

    return None


def _try_parse(source: str) -> ast.AST | None:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _is_python(path: str) -> bool:
    return path.endswith((".py", ".pyi"))
