# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Syntax-tree extraction for Bash safety analysis."""

from __future__ import annotations

from dataclasses import dataclass
import shlex
from threading import local

_PARSER_STATE = local()


@dataclass(frozen=True)
class BashCommand:
    """One executable command extracted from the Bash syntax tree."""

    tokens: tuple[str, ...]
    expanded_variables: tuple[str, ...]
    has_indirect_expansion: bool
    line_number: int
    column: int


@dataclass(frozen=True)
class BashRedirect:
    """One file redirection with a statically recoverable target."""

    operator: str
    target: str | None
    line_number: int
    column: int


@dataclass(frozen=True)
class BashAnalysis:
    """Bounded syntax information consumed by the policy scanner."""

    commands: tuple[BashCommand, ...]
    redirects: tuple[BashRedirect, ...]
    assignments: tuple[tuple[str, str], ...]
    has_command_substitution: bool
    has_process_substitution: bool
    has_background_job: bool
    has_heredoc: bool
    has_unbounded_loop: bool
    has_fork_bomb: bool
    has_parse_error: bool


def _parser():
    """Build the parser lazily so importing the public package stays light."""

    parser = getattr(_PARSER_STATE, "parser", None)
    if parser is not None:
        return parser
    from tree_sitter import Language
    from tree_sitter import Parser
    import tree_sitter_bash

    language = Language(tree_sitter_bash.language())
    parser = Parser(language)
    _PARSER_STATE.language = language
    _PARSER_STATE.parser = parser
    return parser


def _text(source: bytes, node) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _tokens(source: bytes, node) -> tuple[str, ...]:
    command_text = _text(source, node).replace("\\\n", "")
    lexer = shlex.shlex(command_text, posix=True, punctuation_chars=True)
    lexer.commenters = ""
    lexer.whitespace_split = True
    return tuple(lexer)


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def analyze_bash(content: str) -> BashAnalysis:
    """Parse Bash and return only executable syntax, never comments or raw strings."""

    source = content.encode("utf-8")
    root = _parser().parse(source).root_node
    commands: list[BashCommand] = []
    redirects: list[BashRedirect] = []
    assignments: list[tuple[str, str]] = []
    has_parse_error = root.has_error
    all_nodes = tuple(_walk(root))
    unbounded_loop = False
    fork_bomb = False

    for node in all_nodes:
        if node.type == "command":
            try:
                tokens = _tokens(source, node)
            except ValueError:
                has_parse_error = True
                continue
            if tokens:
                expansions = [child for child in _walk(node) if child.type in {"expansion", "simple_expansion"}]
                expanded_variables = tuple(
                    dict.fromkeys(
                        _text(source, child) for expansion in expansions for child in _walk(expansion)
                        if child.type in {"special_variable_name", "variable_name"}))
                commands.append(
                    BashCommand(
                        tokens=tokens,
                        expanded_variables=expanded_variables,
                        has_indirect_expansion=any(
                            _text(source, expansion).startswith("${!") for expansion in expansions),
                        line_number=node.start_point.row + 1,
                        column=node.start_point.column,
                    ))
        elif node.type == "file_redirect":
            operator_node = node.child_by_field_name("operator")
            destination = node.child_by_field_name("destination")
            if operator_node is None:
                operator = next(
                    (_text(source, child) for child in node.children if not child.is_named),
                    "",
                )
            else:
                operator = _text(source, operator_node)
            target: str | None = None
            if destination is not None:
                try:
                    values = _tokens(source, destination)
                except ValueError:
                    has_parse_error = True
                else:
                    if len(values) == 1:
                        target = values[0]
            redirects.append(
                BashRedirect(
                    operator=operator,
                    target=target,
                    line_number=node.start_point.row + 1,
                    column=node.start_point.column,
                ))
        elif node.type == "variable_assignment":
            name = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if name is not None and value is not None:
                try:
                    values = _tokens(source, value)
                except ValueError:
                    has_parse_error = True
                else:
                    if len(values) == 1 and "$" not in values[0] and "`" not in values[0]:
                        assignments.append((_text(source, name), values[0]))
        elif node.type == "while_statement":
            condition = node.child_by_field_name("condition")
            if condition is not None:
                try:
                    condition_tokens = _tokens(source, condition)
                except ValueError:
                    has_parse_error = True
                else:
                    unbounded_loop = unbounded_loop or condition_tokens in {("true", ), (":", ), ("1", )}
        elif node.type == "c_style_for_statement":
            header = _text(source, node).split("do", 1)[0]
            if header.replace(" ", "").startswith("for((;;))"):
                unbounded_loop = True
        elif node.type == "function_definition":
            compact = "".join(_text(source, node).split())
            if ":(){:|:&}" in compact:
                fork_bomb = True

    return BashAnalysis(
        commands=tuple(commands),
        redirects=tuple(redirects),
        assignments=tuple(assignments),
        has_command_substitution=any(node.type == "command_substitution" for node in all_nodes),
        has_process_substitution=any(node.type == "process_substitution" for node in all_nodes),
        has_background_job=any(child.type == "&" for node in all_nodes for child in node.children),
        has_heredoc=any(node.type == "heredoc_redirect" for node in all_nodes),
        has_unbounded_loop=unbounded_loop,
        has_fork_bomb=fork_bomb,
        has_parse_error=has_parse_error,
    )
