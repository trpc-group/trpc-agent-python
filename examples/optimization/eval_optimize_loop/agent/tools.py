# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""被测 agent 的工具（online 模式真实调用；fake/trace 不使用）。"""
from __future__ import annotations

# 演示用图书目录数据库（真实业务替换为远端查询）
_CATALOG: dict[str, dict[str, str]] = {
    "时间简史": {
        "author": "霍金",
        "category": "science",
        "book_id": "BT-000"
    },
    "三体": {
        "author": "刘慈欣",
        "category": "fiction",
        "book_id": "BT-001"
    },
}
_AVAILABILITY: dict[str, str] = {"BT-001": "可借", "BT-000": "已借出"}


def search_catalog(query: str) -> dict:
    """按书名/关键词搜索馆藏目录。"""
    return _CATALOG.get(query, {"author": "未找到", "category": "unknown", "book_id": ""})


def check_availability(book_id: str) -> dict:
    """查询某 book_id 的可借状态。"""
    return {"book_id": book_id, "status": _AVAILABILITY.get(book_id, "未知")}
