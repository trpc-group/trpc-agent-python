# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""summary 专项检测:loss / overwrite / affiliation 三类故障。

三分比较里的「存储元数据」(version / session_id)严格相等,三类故障由本模块显式检测。
「内容语义」预留接口(当前未接入,待需求明确时实现)。
"""

from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel


class SummaryIssue(BaseModel):
    type: Literal["loss", "overwrite", "affiliation"]
    session_id: str
    summary_id: str | None = None
    detail: dict[str, Any]


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
