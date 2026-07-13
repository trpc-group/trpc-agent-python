# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""允许差异规则:JSONPath 精确匹配 + 强制 reason + 覆盖率治理。

规避 ``*.id`` 式过宽通配(会误放业务 id);每条规则必须带 reason;
每 case 的 allowed 条数与占比有上限,防「用 allowed_diff 塞进真不一致」。

用 token 化逐段匹配,避开 fnmatch 字符集陷阱(``[*]`` 会被 fnmatch 当成
「匹配单个 * 字符」的字符集,而非下标通配)。
"""

from __future__ import annotations

import re

from .harness import AllowedDiffRule
from .harness import ReplayCase

MAX_ALLOWED_PER_CASE = 8
"""每 case allowed_diff 规则条数上限。"""

MAX_ALLOWED_RATIO = 0.10
"""allowed 字段占该 case 总比较字段的比例上限。"""

_PATH_TOKEN = re.compile(r"[\w:]+|\[\d+\]|\[\*\]")


def _tokenize_path(path: str) -> list[tuple[str, str]]:
    """``events[0].author`` → ``[("key","events"),("idx","0"),("key","author")]``。"""
    tokens: list[tuple[str, str]] = []
    for chunk in _PATH_TOKEN.findall(path):
        if chunk.startswith("["):
            tokens.append(("idx", chunk[1:-1]))  # 数字或 "*"
        else:
            tokens.append(("key", chunk))
    return tokens


def _match_tokens(field_tokens: list[tuple[str, str]], rule_tokens: list[tuple[str, str]]) -> bool:
    if len(field_tokens) != len(rule_tokens):
        return False
    for (fkind, fval), (rkind, rval) in zip(field_tokens, rule_tokens):
        if fkind != rkind:
            return False
        if rval == "*":  # 下标通配(idx 的 *)
            continue
        if fval != rval:
            return False
    return True


def is_allowed(
    field_path: str,
    backend_pair: tuple[str, str],
    rules: list[AllowedDiffRule],
) -> tuple[bool, str | None]:
    """判断字段差异是否被规则允许。无 reason 的规则不生效。"""
    field_tokens = _tokenize_path(field_path)
    for rule in rules:
        if not rule.reason.strip():
            continue
        if rule.backend_pair and tuple(rule.backend_pair) != tuple(backend_pair):
            continue
        if _match_tokens(field_tokens, _tokenize_path(rule.path)):
            return True, rule.reason
    return False, None


def check_governance(case: ReplayCase, total_fields: int, used_allowed: int) -> None:
    """治理:超限或无 reason 即抛错。由 test_allowed_diff_governance 强制。"""
    for rule in case.allowed_diff:
        if not rule.reason.strip():
            raise ValueError(f"allowed_diff rule without reason: {rule.path}")
    if len(case.allowed_diff) > MAX_ALLOWED_PER_CASE:
        raise ValueError(f"too many allowed_diff rules: {len(case.allowed_diff)} > {MAX_ALLOWED_PER_CASE}")
    if total_fields > 0 and used_allowed / total_fields > MAX_ALLOWED_RATIO:
        raise ValueError(f"allowed ratio too high: {used_allowed}/{total_fields} > {MAX_ALLOWED_RATIO}")
