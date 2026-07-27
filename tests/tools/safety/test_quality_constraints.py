# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Executable quality constraints for the safety package."""

import ast
from pathlib import Path

MAX_FILE_LINES = 1000
MAX_FUNCTION_LINES = 80
MAX_FUNCTION_STATEMENTS = 60
MAX_FUNCTION_PARAMETERS = 4
REPO_ROOT = Path(__file__).resolve().parents[3]
SAFETY_PACKAGE = REPO_ROOT / "trpc_agent_sdk/tools/safety"


def _functions(tree):
    return [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _parameter_count(node):
    args = node.args
    counts = (
        len(args.posonlyargs),
        len(args.args),
        len(args.kwonlyargs),
        int(args.vararg is not None),
        int(args.kwarg is not None),
    )
    return sum(counts)


def _statement_count(node):
    return sum(isinstance(child, ast.stmt) for child in ast.walk(node)) - 1


def test_source_size_and_function_limits():
    failures = []
    paths = list(SAFETY_PACKAGE.glob("*.py"))
    assert paths, f"no Python files found under {SAFETY_PACKAGE}"
    for path in paths:
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        if len(lines) > MAX_FILE_LINES:
            failures.append(f"{path}: file has {len(lines)} lines")
        tree = ast.parse(source)
        for function in _functions(tree):
            span = function.end_lineno - function.lineno + 1
            statements = _statement_count(function)
            parameters = _parameter_count(function)
            if span > MAX_FUNCTION_LINES:
                failures.append(f"{path}:{function.lineno} {function.name}: {span} lines")
            if statements > MAX_FUNCTION_STATEMENTS:
                failures.append(f"{path}:{function.lineno} {function.name}: {statements} statements")
            if parameters > MAX_FUNCTION_PARAMETERS:
                failures.append(f"{path}:{function.lineno} {function.name}: {parameters} parameters")
    assert not failures, "\n".join(failures)
