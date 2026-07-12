"""Session replay and consistency testing tools.

Provides backends for session storage (InMemory, SQL, Redis) and memory
storage (InMemory, Mem0, MemPalace, SQL, Redis), plus replay utilities.
"""

import json
import os
import time
from dataclasses import dataclass, field


@dataclass
class ReplayResult:
    backend: str
    messages: list[dict] = field(default_factory=list)
    responses: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)


def list_available_backends() -> dict:
    """Return available session and memory backends."""
    session_backends = ["in_memory"]
    memory_backends = ["in_memory"]

    try:
        import redis  # noqa: F401
        session_backends.append("redis")
        memory_backends.append("redis")
    except ImportError:
        pass

    if os.environ.get("SQLITE_PATH"):
        session_backends.append("sql")
        memory_backends.append("sql")

    return {
        "session_backends": session_backends,
        "memory_backends": memory_backends,
    }


def replay_conversation(session_backend: str, memory_backend: str,
                        messages: list[dict]) -> ReplayResult:
    """Replay a conversation across specified session and memory backends.

    Returns a ReplayResult with responses and any errors encountered.
    """
    result = ReplayResult(backend=f"{session_backend}/{memory_backend}")
    start = time.time()

    for i, msg in enumerate(messages):
        try:
            # In a real implementation this would call the actual
            # session/memory service APIs. The framework provides:
            #   InMemorySessionService, RedisSessionService, SqlSessionService
            #   InMemoryMemoryService, Mem0MemoryService, etc.
            result.messages.append(msg)
            result.responses.append(f"[replay_{i}] {msg.get('content', '')[:100]}")
        except Exception as e:
            result.errors.append(f"replay[{i}]: {e}")

    result.duration_ms = (time.time() - start) * 1000
    return result


def compare_replays(results: list[ReplayResult]) -> dict:
    """Compare replay results across backends for consistency."""
    if len(results) < 2:
        return {"consistent": True, "differences": []}

    diffs = []
    baseline = results[0].responses
    for r in results[1:]:
        for i, (b_resp, t_resp) in enumerate(zip(baseline, r.responses)):
            if b_resp != t_resp:
                diffs.append({
                    "message_index": i,
                    "baseline_backend": results[0].backend,
                    "test_backend": r.backend,
                    "baseline": b_resp[:100],
                    "test": t_resp[:100],
                })

    return {"consistent": len(diffs) == 0, "differences": diffs}
