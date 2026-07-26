#!/usr/bin/env python3
"""
End-to-end replay consistency demo.

Demonstrates how the replay consistency framework detects real-world
anomalies between InMemory and SQLite backends.

Usage:
    cd examples/replay_consistency_demo

    # 基本模式（无需 API Key）
    python replay_consistency_demo.py

    # 真实模型模式（需设置 DEEPSEEK_API_KEY）
    export DEEPSEEK_API_KEY="sk-你的key"
    python replay_consistency_demo.py --real-model

Scenarios:
    1. Normal:  Same events on both backends → PASS
    2. Event order mismatch:  Events reordered on SQLite → FAIL
    3. Text tampering:  Event text altered on SQLite → FAIL
    4. Missing event:  SQLite drops one event → FAIL
    5. Real model:  DeepSeek generates responses, backends compared → PASS/FAIL
"""

import argparse
import asyncio
import sys
import os

# Ensure the project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import (
    InMemorySessionService,
    SessionServiceConfig,
    SqlSessionService,
)
from trpc_agent_sdk.types import Content, Part


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Read API key from environment (never hardcode!)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def make_clean_backends():
    """Create a fresh InMemory + SQLite backend pair."""
    config = SessionServiceConfig()
    config.clean_ttl_config()

    inmem = InMemorySessionService(session_config=config.model_copy(deep=True))
    sqlite = SqlSessionService(
        db_url="sqlite:///:memory:",
        session_config=config.model_copy(deep=True),
        is_async=False,
    )
    await sqlite._sql_storage.create_sql_engine()
    return inmem, sqlite


def make_event(author: str, text: str, parts=None):
    """Create a simple text event."""
    content_parts = parts or [Part(text=text)]
    return Event(
        author=author,
        content=Content(parts=content_parts, role=author),
    )


def normalize_session(session, normalize_events=True):
    """Normalize a session for comparison (minimal version)."""
    if session is None:
        return None
    result = {
        "session_id": session.id,
        "state": dict(session.state) if session.state else {},
    }
    if normalize_events and session.events:
        events = []
        for evt in session.events:
            parts = evt.content.parts if evt.content else []
            e = {
                "author": evt.author,
                "parts": [{"text": p.text} for p in parts if p.text],
            }
            events.append(e)
        result["events"] = events
    else:
        result["events"] = []
    return result


def compare_sessions(label: str, expected, actual) -> list[str]:
    """Compare two normalized sessions and return a list of diffs."""
    diffs = []
    exp_events = expected.get("events", [])
    act_events = actual.get("events", [])

    if len(exp_events) != len(act_events):
        diffs.append(f"  ❌ Event count: expected {len(exp_events)}, got {len(act_events)}")

    for i in range(min(len(exp_events), len(act_events))):
        ee = exp_events[i]
        ae = act_events[i]
        if ee.get("author") != ae.get("author"):
            diffs.append(f"  ❌ events[{i}].author: expected '{ee.get('author')}', got '{ae.get('author')}'")
        e_text = " ".join(p.get("text", "") for p in ee.get("parts", []))
        a_text = " ".join(p.get("text", "") for p in ae.get("parts", []))
        if e_text != a_text:
            diffs.append(f"  ❌ events[{i}].text differs")
            diffs.append(f"      expected: {e_text[:80]}")
            diffs.append(f"      actual:   {a_text[:80]}")

    if not diffs:
        diffs.append("  ✅ All events match")
    return diffs


# ---------------------------------------------------------------------------
# Scenario 1: Normal — identical events on both backends
# ---------------------------------------------------------------------------

async def scenario_normal():
    print("\n" + "=" * 60)
    print("Scenario 1: Normal — identical events on both backends")
    print("=" * 60)

    inmem, sqlite = await make_clean_backends()
    session_config = SessionServiceConfig()
    session_config.clean_ttl_config()

    # Create identical sessions
    inmem_session = await inmem.create_session(app_name="test_app", user_id="test_user")
    sqlite_session = await sqlite.create_session(app_name="test_app", user_id="test_user")

    # Append identical events
    messages = [
        ("user", "Hello, what is the weather in Tokyo?"),
        ("assistant", "Let me check the weather for you."),
        ("tool_call", 'get_weather(city="Tokyo")'),
        ("tool_response", '{"temperature": 22, "condition": "sunny"}'),
        ("assistant", "The weather in Tokyo is 22°C and sunny."),
    ]

    for author, text in messages:
        event = make_event(author, text)
        await inmem.append_event(inmem_session, event)
        await sqlite.append_event(sqlite_session, event)

    # Fetch and compare
    inmem_fetched = await inmem.get_session(app_name="test_app", user_id="test_user", session_id=inmem_session.id)
    sqlite_fetched = await sqlite.get_session(app_name="test_app", user_id="test_user", session_id=sqlite_session.id)

    n_inmem = normalize_session(inmem_fetched)
    n_sqlite = normalize_session(sqlite_fetched)

    diffs = compare_sessions("Normal", n_inmem, n_sqlite)
    for d in diffs:
        print(d)

    if all("❌" not in d for d in diffs):
        print("  ✅ Scenario 1 PASSED — backends are consistent")
    else:
        print("  ❌ Scenario 1 FAILED — unexpected inconsistency")

    await inmem.close()
    await sqlite.close()
    return diffs


# ---------------------------------------------------------------------------
# Scenario 2: Event order mismatch — reorder events on SQLite
# ---------------------------------------------------------------------------

async def scenario_event_order_mismatch():
    print("\n" + "=" * 60)
    print("Scenario 2: Event order mismatch — events reordered on SQLite")
    print("=" * 60)

    inmem, sqlite = await make_clean_backends()
    session_config = SessionServiceConfig()
    session_config.clean_ttl_config()

    inmem_session = await inmem.create_session(app_name="test_app", user_id="test_user")
    sqlite_session = await sqlite.create_session(app_name="test_app", user_id="test_user")

    messages = [
        ("user", "What is the capital of France?"),
        ("assistant", "The capital is Paris."),
        ("user", "What about Japan?"),
        ("assistant", "The capital is Tokyo."),
    ]

    # Append in correct order to InMemory
    for author, text in messages:
        await inmem.append_event(inmem_session, make_event(author, text))

    # Append in WRONG order to SQLite (swap events 2 and 3)
    for author, text in [messages[0], messages[1], messages[3], messages[2]]:
        await sqlite.append_event(sqlite_session, make_event(author, text))

    inmem_fetched = await inmem.get_session(app_name="test_app", user_id="test_user", session_id=inmem_session.id)
    sqlite_fetched = await sqlite.get_session(app_name="test_app", user_id="test_user", session_id=sqlite_session.id)

    n_inmem = normalize_session(inmem_fetched)
    n_sqlite = normalize_session(sqlite_fetched)

    diffs = compare_sessions("EventOrder", n_inmem, n_sqlite)
    for d in diffs:
        print(d)

    if any("❌" in d for d in diffs):
        print("  ✅ Scenario 2 PASSED — anomaly correctly detected")
    else:
        print("  ❌ Scenario 2 FAILED — anomaly was NOT detected")

    await inmem.close()
    await sqlite.close()
    return diffs


# ---------------------------------------------------------------------------
# Scenario 3: Text tampering — alter event text on SQLite
# ---------------------------------------------------------------------------

async def scenario_text_tampering():
    print("\n" + "=" * 60)
    print("Scenario 3: Text tampering — event text altered on SQLite")
    print("=" * 60)

    inmem, sqlite = await make_clean_backends()
    session_config = SessionServiceConfig()
    session_config.clean_ttl_config()

    inmem_session = await inmem.create_session(app_name="test_app", user_id="test_user")
    sqlite_session = await sqlite.create_session(app_name="test_app", user_id="test_user")

    # Same event on both
    original_text = "Transfer $1000 to account 12345"
    await inmem.append_event(inmem_session, make_event("user", original_text))
    await sqlite.append_event(sqlite_session, make_event("user", original_text))

    # Tamper with SQLite's event directly
    if sqlite_session.events[-1].content and sqlite_session.events[-1].content.parts:
        sqlite_session.events[-1].content.parts[0].text = "Transfer $999999 to account 99999"
    await sqlite.update_session(sqlite_session)

    inmem_fetched = await inmem.get_session(app_name="test_app", user_id="test_user", session_id=inmem_session.id)
    sqlite_fetched = await sqlite.get_session(app_name="test_app", user_id="test_user", session_id=sqlite_session.id)

    n_inmem = normalize_session(inmem_fetched)
    n_sqlite = normalize_session(sqlite_fetched)

    diffs = compare_sessions("TextTamper", n_inmem, n_sqlite)
    for d in diffs:
        print(d)

    if any("❌" in d for d in diffs):
        print("  ✅ Scenario 3 PASSED — text tampering correctly detected")
    else:
        print("  ❌ Scenario 3 FAILED — text tampering was NOT detected")

    await inmem.close()
    await sqlite.close()
    return diffs


# ---------------------------------------------------------------------------
# Scenario 4: Missing event — SQLite drops one event
# ---------------------------------------------------------------------------

async def scenario_missing_event():
    print("\n" + "=" * 60)
    print("Scenario 4: Missing event — SQLite drops one event")
    print("=" * 60)

    inmem, sqlite = await make_clean_backends()
    session_config = SessionServiceConfig()
    session_config.clean_ttl_config()

    inmem_session = await inmem.create_session(app_name="test_app", user_id="test_user")
    sqlite_session = await sqlite.create_session(app_name="test_app", user_id="test_user")

    messages = [
        ("user", "Step 1: Login"),
        ("user", "Step 2: Authenticate"),
        ("user", "Step 3: Transfer funds"),
        ("user", "Step 4: Confirm"),
    ]

    for author, text in messages:
        await inmem.append_event(inmem_session, make_event(author, text))

    # SQLite misses "Step 3: Transfer funds"
    for idx in [0, 1, 3]:
        await sqlite.append_event(sqlite_session, make_event(messages[idx][0], messages[idx][1]))

    inmem_fetched = await inmem.get_session(app_name="test_app", user_id="test_user", session_id=inmem_session.id)
    sqlite_fetched = await sqlite.get_session(app_name="test_app", user_id="test_user", session_id=sqlite_session.id)

    n_inmem = normalize_session(inmem_fetched)
    n_sqlite = normalize_session(sqlite_fetched)

    diffs = compare_sessions("MissingEvent", n_inmem, n_sqlite)
    for d in diffs:
        print(d)

    if any("❌" in d for d in diffs):
        print("  ✅ Scenario 4 PASSED — missing event correctly detected")
    else:
        print("  ❌ Scenario 4 FAILED — missing event was NOT detected")

    await inmem.close()
    await sqlite.close()
    return diffs


# ---------------------------------------------------------------------------
# Scenario 5: Real model — DeepSeek generates responses, compare backends
# ---------------------------------------------------------------------------

async def scenario_real_model():
    """Run a real conversation through DeepSeek model, persist events
    to both InMemory and SQLite backends, then compare for consistency.

    This demonstrates the mentor's requirement of using a real model
    to drive the replay consistency framework.
    Uses OpenAI SDK directly (DeepSeek official format).
    """
    print("\n" + "=" * 60)
    print("Scenario 5: Real model — DeepSeek generates responses")
    print("=" * 60)
    print(f"  Model: {DEEPSEEK_MODEL}")
    print(f"  Base:  {DEEPSEEK_BASE_URL}")
    print()

    if not DEEPSEEK_API_KEY:
        print("  ⏭️  SKIPPED — DEEPSEEK_API_KEY not set")
        print("  Set it with: export DEEPSEEK_API_KEY='sk-...'")
        print("  Or use: python replay_consistency_demo.py (without --real-model)")
        return ["  ⏭️  SKIPPED"]

    # Use OpenAI SDK directly (DeepSeek-compatible format)
    from openai import OpenAI

    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )

    # DeepSeek official tool format: list of dicts with "type": "function"
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "获取指定城市的天气信息",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "城市名称"},
                    },
                    "required": ["city"],
                },
            },
        },
    ]

    # Mock tool execution results
    weather_data = {
        "Tokyo": {"temperature": "22°C", "condition": "Sunny", "humidity": "45%"},
        "东京": {"temperature": "22°C", "condition": "Sunny", "humidity": "45%"},
        "Beijing": {"temperature": "15°C", "condition": "Cloudy", "humidity": "60%"},
        "北京": {"temperature": "15°C", "condition": "Cloudy", "humidity": "60%"},
    }

    def execute_tool(name: str, args: dict) -> str:
        """Execute a tool function and return the result string."""
        if name == "get_weather":
            city = args.get("city", "")
            wd = weather_data.get(city, {})
            return f"{wd.get('temperature', 'N/A')}, {wd.get('condition', 'N/A')}"
        return f"Unknown tool: {name}"

    # Create backends
    inmem, sqlite = await make_clean_backends()
    inmem_session = await inmem.create_session(app_name="test_app", user_id="test_user")
    sqlite_session = await sqlite.create_session(app_name="test_app", user_id="test_user")

    # ── Standard tool calling flow ──
    # Step 1: User asks a question
    user_msg = "How's the weather in Tokyo?"
    user_event = make_event("user", user_msg)
    await inmem.append_event(inmem_session, user_event)
    await sqlite.append_event(sqlite_session, user_event)

    # Build messages list for OpenAI API
    messages = [{"role": "user", "content": user_msg}]

    # Step 2: Call model with tools
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        tools=tools,
    )
    assistant_msg = response.choices[0].message

    # Step 3: Check if model made a tool call
    final_reply = ""
    if assistant_msg.tool_calls:
        for tool_call in assistant_msg.tool_calls:
            import json
            func_name = tool_call.function.name
            func_args = json.loads(tool_call.function.arguments)

            # Persist tool_call event to both backends
            tc_text = f'{func_name}({json.dumps(func_args)})'
            tc_event = make_event("tool_call", tc_text)
            await inmem.append_event(inmem_session, tc_event)
            await sqlite.append_event(sqlite_session, tc_event)

            # Execute tool
            tool_result = execute_tool(func_name, func_args)
            print(f"  🔧 Tool call: {tc_text} → {tool_result}")

            # Persist tool_response event to both backends
            tr_event = make_event("tool_response", tool_result)
            await inmem.append_event(inmem_session, tr_event)
            await sqlite.append_event(sqlite_session, tr_event)

            # Step 4: Return tool result to model for final answer
            messages.append(assistant_msg)  # assistant message with tool_calls
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_result,
            })

        # Step 5: Model generates final answer with tool result
        final_response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            tools=tools,
        )
        final_reply = final_response.choices[0].message.content or ""
    else:
        # Model answered directly without tool call
        final_reply = assistant_msg.content or ""

    # Persist final assistant response to both backends
    assistant_event = make_event("assistant", final_reply)
    await inmem.append_event(inmem_session, assistant_event)
    await sqlite.append_event(sqlite_session, assistant_event)

    print(f"  User: {user_msg}")
    print(f"  Model: {final_reply[:80]}...")
    print()

    # Fetch and compare
    inmem_fetched = await inmem.get_session(app_name="test_app", user_id="test_user", session_id=inmem_session.id)
    sqlite_fetched = await sqlite.get_session(app_name="test_app", user_id="test_user", session_id=sqlite_session.id)

    n_inmem = normalize_session(inmem_fetched)
    n_sqlite = normalize_session(sqlite_fetched)

    diffs = compare_sessions("RealModel", n_inmem, n_sqlite)
    for d in diffs:
        print(d)

    if all("❌" not in d for d in diffs):
        print("  ✅ Scenario 5 PASSED — backends are consistent with real model")
    else:
        print("  ❌ Scenario 5 FAILED — backends diverged with real model")

    await inmem.close()
    await sqlite.close()
    return diffs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Replay Consistency Demo")
    parser.add_argument("--real-model", action="store_true",
                        help="Run with real DeepSeek model (requires DEEPSEEK_API_KEY)")
    args = parser.parse_args()

    print("=" * 60)
    print("Replay Consistency Demo")
    print("Demonstrates cross-backend anomaly detection")
    print("=" * 60)

    results = await asyncio.gather(
        scenario_normal(),
        scenario_event_order_mismatch(),
        scenario_text_tampering(),
        scenario_missing_event(),
    )

    if args.real_model:
        real_result = await scenario_real_model()
        results.append(real_result)

    passed = sum(1 for r in results if all("❌" not in d for d in r))
    failed = len(results) - passed

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Scenarios: {len(results)}")
    print(f"  Passed:    {passed}")
    print(f"  Failed:    {failed}")

    # Count anomalies detected (excluding scenario 1 and skipped)
    anomaly_scenarios = [r for r in results[1:] if all("⏭️" not in d for d in r)]
    anomalies_detected = sum(1 for r in anomaly_scenarios if any("❌" in d for d in r))
    print(f"  Anomalies detected: {anomalies_detected}/{len(anomaly_scenarios)}")
    print()

    if failed == 0:
        print("✅ All scenarios passed — framework correctly identifies anomalies")
    else:
        print(f"❌ {failed} scenario(s) failed — review needed")

    return passed == len(results)


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
