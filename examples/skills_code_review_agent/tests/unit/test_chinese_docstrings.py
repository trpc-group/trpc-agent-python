#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""验证项目生产代码的函数级中文说明约定。"""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _functions_without_chinese_docstrings(path: Path) -> list[str]:
    """返回指定生产源码中缺少中文函数说明的函数位置。"""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        docstring = ast.get_docstring(node, clean=False) or ""
        if not any("\u4e00" <= character <= "\u9fff" for character in docstring):
            missing.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}:{node.name}")
    return missing


def test_all_product_functions_have_chinese_docstrings() -> None:
    """要求产品代码和随 Skill 分发的脚本均提供中文函数级说明。"""

    source_files = sorted(
        path
        for path in PROJECT_ROOT.rglob("*.py")
        if "tests" not in path.parts and "__pycache__" not in path.parts
    )
    missing = [
        location
        for source_file in source_files
        for location in _functions_without_chinese_docstrings(source_file)
    ]

    assert not missing, "missing_chinese_docstrings:\n" + "\n".join(missing)
