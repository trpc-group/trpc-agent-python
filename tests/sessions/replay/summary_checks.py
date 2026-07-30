# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""summary 专项检测:loss / overwrite / affiliation 三类故障 + 文本语义相似度工具。

三分比较里的「存储元数据」(version / session_id)严格相等,三类故障由本模块显式检测。
``summary_text_similarity`` 为纯工具函数(已单测覆盖),预留语义比较接口;
``SUMMARY_SIM_THRESHOLD`` 已删除 —— 该常量此前无任何调用方(helloopenworld review)。
"""

from __future__ import annotations

import re
from typing import Any
from typing import Literal

from pydantic import BaseModel


class SummaryIssue(BaseModel):
    type: Literal["loss", "overwrite", "affiliation"]
    session_id: str
    summary_id: str | None = None
    detail: dict[str, Any]


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def summary_text_similarity(a: str | None, b: str | None) -> float:
    """分词集合 Jaccard 相似度。任一空串返回 0.0。

    纯工具函数(已单测覆盖);当前未接入 check_summary_issues 主流程,
    待「内容语义比较」需求明确后再决定是否接入(helloopenworld review)。
    """
    if not a or not b:
        return 0.0
    ta = set(_tokenize(a))
    tb = set(_tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def check_summary_issues(
    reference_summary: dict[str, Any],
    candidate_summary: dict[str, Any],
    *,
    candidate_backend: str,
    session_id: str,
) -> list[SummaryIssue]:
    """检测 summary 三类故障:loss / overwrite(version 倒退)/ affiliation(session 归属错)。"""
    issues: list[SummaryIssue] = []
    ref_cur = reference_summary.get("current") if reference_summary else None
    cand_cur = candidate_summary.get("current") if candidate_summary else None

    # loss:参考端有 summary,候选端丢失。
    if ref_cur and not cand_cur:
        issues.append(SummaryIssue(type="loss", session_id=session_id, detail={"backend": candidate_backend}))
        return issues

    if not (ref_cur and cand_cur):
        return issues

    # overwrite:候选 version 倒退(旧版覆盖新版)。
    ref_ver = ref_cur.get("version")
    cand_ver = cand_cur.get("version")
    if ref_ver is not None and cand_ver is not None and cand_ver < ref_ver:
        issues.append(
            SummaryIssue(
                type="overwrite",
                session_id=session_id,
                summary_id=cand_cur.get("id"),
                detail={
                    "ref_version": ref_ver,
                    "cand_version": cand_ver,
                    "backend": candidate_backend,
                },
            ))

    # affiliation:summary 归属 session 错误。
    ref_sid = ref_cur.get("session_id")
    cand_sid = cand_cur.get("session_id")
    if ref_sid and cand_sid and ref_sid != cand_sid:
        issues.append(
            SummaryIssue(
                type="affiliation",
                session_id=session_id,
                summary_id=cand_cur.get("id"),
                detail={
                    "ref_session": ref_sid,
                    "cand_session": cand_sid,
                    "backend": candidate_backend,
                },
            ))

    return issues
