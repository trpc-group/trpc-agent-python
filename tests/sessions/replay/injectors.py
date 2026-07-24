# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""检出验证:快照层注入 + 端到端后端注入。

快照层(deepcopy 改字段)对齐 10 个 PR;端到端(改 SQL 行 / Redis key 后重读)
是本设计的创新 —— 验证 harness 对真实后端数据漂移的感知能力。
"""

from __future__ import annotations

import json

from .harness import ReplaySnapshot

# 快照层注入种类 —— 覆盖 event/state/memory/summary 四类。
SNAPSHOT_INJECTION_KINDS = (
    "event_author",
    "event_text",
    "extra_event",
    "state_value",
    "memory_content",
    "summary_loss",
    "summary_overwrite",
    "summary_affiliation",
)


def inject_snapshot_diff(snapshot: ReplaySnapshot, kind: str) -> ReplaySnapshot:
    """快照层:deepcopy 改字段,验证比较器检出率。"""
    snap = snapshot.model_copy(deep=True)
    if kind == "event_author":
        if snap.events:
            snap.events[0]["author"] = "INJECTED"
    elif kind == "event_text":
        content = snap.events[0].get("content") if snap.events else None
        if content and content.get("parts"):
            content["parts"][0]["text"] = "INJECTED"
    elif kind == "extra_event":
        snap.events.append({"author": "INJECTED", "content": {"parts": [{"text": "x"}]}})
    elif kind == "state_value":
        if snap.state:
            snap.state[next(iter(snap.state))] = "INJECTED"
    elif kind == "memory_content":
        if snap.memory:
            key = next(iter(snap.memory))
            if snap.memory[key]:
                snap.memory[key][0] = {"content": "INJECTED"}
    elif kind == "summary_loss":
        snap.summary = {"current": None}
    elif kind == "summary_overwrite":
        cur = snap.summary.get("current")
        if cur:
            cur["version"] = 0  # 倒退
    elif kind == "summary_affiliation":
        cur = snap.summary.get("current")
        if cur:
            cur["session_id"] = "wrong-session"
    return snap


def inject_sql_diff(
    db_url: str,
    app_name: str,
    user_id: str,
    session_id: str,
    kind: str = "event_author",
) -> bool:
    """端到端 SQL:直接 UPDATE 行,绕过 service 缓存。返回是否成功注入。"""
    from sqlalchemy import create_engine
    from sqlalchemy import text

    engine = create_engine(db_url)
    injected = False
    with engine.begin() as conn:
        if kind == "event_author":
            conn.execute(
                text("UPDATE events SET author = :v WHERE session_id = :sid"),
                {
                    "v": "INJECTED-SQL",
                    "sid": session_id
                },
            )
            injected = True
        elif kind == "state_value":
            conn.execute(
                text("UPDATE OR REPLACE app_states "
                     "SET state = json_set(state, '$.injected', :v) WHERE app_name = :a"),
                {
                    "v": '"INJECTED"',
                    "a": app_name
                },
            )
            injected = True
    return injected


def inject_redis_diff(
    redis_url: str,
    app_name: str,
    user_id: str,
    session_id: str,
    kind: str = "event_author",
) -> bool:
    """端到端 Redis:SET / HSET 改 key。需要真实 Redis 可达。"""
    import redis

    client = redis.from_url(redis_url)
    injected = False
    if kind == "event_author":
        key = f"session:{app_name}:{user_id}:{session_id}"
        raw = client.get(key)
        if raw:
            data = json.loads(raw)
            if data.get("events"):
                data["events"][0]["author"] = "INJECTED-REDIS"
                client.set(key, json.dumps(data))
                injected = True
    elif kind == "state_value":
        client.hset(f"app_state:{app_name}", "injected", "INJECTED")
        injected = True
    return injected
