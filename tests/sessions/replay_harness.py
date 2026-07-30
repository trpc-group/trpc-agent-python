"""Reusable Session / Memory / Summary replay consistency harness."""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.types import Content, EventActions, FunctionCall, FunctionResponse, Part


VOLATILE_FIELDS = {"timestamp", "last_update_time", "summary_timestamp"}


@dataclass
class ReplayBackend:
    name: str
    session_service: Any
    memory_service: Any = field(default_factory=lambda: InMemoryMemoryService(enabled=True))
    summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    memory_queries: list[str] = field(default_factory=list)
    seen_event_ids: set[str] = field(default_factory=set)

    async def close(self) -> None:
        await self.memory_service.close()
        await self.session_service.close()


def load_cases(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _event(operation: dict[str, Any], index: int) -> Event:
    op = operation["op"]
    if op == "tool_call":
        part = Part(function_call=FunctionCall(id=operation["id"], name=operation["name"], args=operation["args"]))
        author = "agent"
    elif op == "tool_response":
        part = Part(function_response=FunctionResponse(id=operation["id"], name=operation["name"],
                                                       response=operation["response"]))
        author = "tool"
    else:
        part = Part.from_text(text=operation.get("text", ""))
        author = operation.get("author", "agent")
    actions = EventActions(state_delta=operation.get("values", {}))
    content = None if op == "state" else Content(parts=[part])
    return Event(id=operation["id"], invocation_id=f"inv-{index}", author=author,
                 timestamp=time.time() + index, content=content, actions=actions)


async def replay_case(backend: ReplayBackend, case: dict[str, Any]) -> dict[str, Any]:
    app_name, user_id, session_id = "replay", "fixture-user", case["name"]
    backend.memory_queries.clear()
    session = await backend.session_service.create_session(app_name=app_name, user_id=user_id,
                                                           session_id=session_id)
    for index, operation in enumerate(case["operations"]):
        op = operation["op"]
        if op in {"event", "tool_call", "tool_response", "state"}:
            # Retries are idempotent by the stable input event id. This is deliberately
            # enforced at the harness boundary because backend duplicate semantics differ.
            replay_event_id = f"{session_id}:{operation['id']}"
            if replay_event_id in backend.seen_event_ids:
                continue
            backend.seen_event_ids.add(replay_event_id)
            await backend.session_service.append_event(session, _event(operation, index))
        elif op == "memory":
            backend.memory_queries.append(operation["query"])
        elif op == "summary":
            backend.summaries[session_id] = {
                "id": operation["id"], "session_id": session_id,
                "version": operation["version"], "text": operation["text"],
                "summary_timestamp": 1_700_000_100.0 + index,
            }
        elif op == "truncate":
            stored = await backend.session_service.get_session(app_name=app_name, user_id=user_id,
                                                               session_id=session_id)
            stored.historical_events.extend(stored.events[:-operation["keep"] or None])
            stored.events = stored.events[-operation["keep"]:] if operation["keep"] else []
            await backend.session_service.update_session(stored)
        elif op == "fail":
            continue  # simulated failure occurs before a storage call

    stored = await backend.session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
    await backend.memory_service.store_session(stored)
    memories = []
    for query in backend.memory_queries:
        result = await backend.memory_service.search_memory(stored.save_key, query)
        memories.extend(item.model_dump(mode="json", exclude_none=True) for item in result.memories)
    return {
        "session_id": stored.id,
        "events": [event.model_dump(mode="json", exclude_none=True) for event in stored.events],
        "historical_events": [event.model_dump(mode="json", exclude_none=True) for event in stored.historical_events],
        "state": stored.state,
        "memory": memories,
        "summary": backend.summaries.get(session_id),
    }


def normalize(value: Any, path: str = "", allowed_diff: set[str] | None = None) -> Any:
    """Remove only explicitly volatile fields and canonicalize summary text/order."""
    allowed_diff = allowed_diff or set()
    if isinstance(value, dict):
        result = {}
        for key in sorted(value):
            child_path = f"{path}.{key}" if path else key
            if key in VOLATILE_FIELDS or child_path in allowed_diff:
                continue
            child = normalize(value[key], child_path, allowed_diff)
            if key == "long_running_tool_ids" and child in (None, []):
                continue
            if key == "text" and path.endswith("summary") and isinstance(child, str):
                child = re.sub(r"\s+", " ", child).strip()
            result[key] = child
        return result
    if isinstance(value, list):
        return [normalize(item, f"{path}[{index}]", allowed_diff) for index, item in enumerate(value)]
    return value


def diff_values(left: Any, right: Any, path: str = "") -> list[dict[str, Any]]:
    """Return leaf differences with an actionable JSON-style field path."""
    if type(left) is not type(right):
        return [{"path": path or "$", "left": left, "right": right}]
    if isinstance(left, dict):
        diffs = []
        for key in sorted(set(left) | set(right)):
            child_path = f"{path}.{key}" if path else key
            if key not in left or key not in right:
                diffs.append({"path": child_path, "left": left.get(key), "right": right.get(key)})
            else:
                diffs.extend(diff_values(left[key], right[key], child_path))
        return diffs
    if isinstance(left, list):
        diffs = []
        for index in range(max(len(left), len(right))):
            child_path = f"{path}[{index}]"
            if index >= len(left) or index >= len(right):
                diffs.append({"path": child_path,
                              "left": left[index] if index < len(left) else None,
                              "right": right[index] if index < len(right) else None})
            else:
                diffs.extend(diff_values(left[index], right[index], child_path))
        return diffs
    return [] if left == right else [{"path": path or "$", "left": left, "right": right}]


def compare_snapshots(left: dict[str, Any], right: dict[str, Any],
                      allowed_diff: set[str] | None = None) -> list[dict[str, Any]]:
    return diff_values(normalize(left, allowed_diff=allowed_diff),
                       normalize(right, allowed_diff=allowed_diff))


MUTATIONS: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    ("event_text", lambda s: s["events"][0]["content"]["parts"][0].update(text="corrupt")),
    ("event_order", lambda s: s["events"].reverse()),
    ("event_missing", lambda s: s["events"].pop()),
    ("state_value", lambda s: s["state"].update(theme="corrupt")),
    ("memory_content", lambda s: s["memory"][0]["content"]["parts"][0].update(text="corrupt")),
    ("memory_author", lambda s: s["memory"][0].update(author="corrupt")),
    ("summary_missing", lambda s: s.update(summary=None)),
    ("summary_text", lambda s: s["summary"].update(text="corrupt")),
    ("summary_session", lambda s: s["summary"].update(session_id="wrong-session")),
    ("summary_version", lambda s: s["summary"].update(version=999)),
]


def mutate(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    mutated = copy.deepcopy(snapshot)
    dict(MUTATIONS)[name](mutated)
    return mutated
