# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from trpc_agent_sdk.sessions import InMemorySessionService


async def print_session_state(session_service: InMemorySessionService, app_name: str, user_id: str, session_id: str,
                              title: str):
    """Print current state for debugging.
    Args:
        session_service: InMemorySessionService, the session service
        app_name: str, the name of the app
        user_id: str, the id of the user
        session_id: str, the id of the session
        title: str, the title of the state
    """
    print(f"\n📊 {title}")
    print(f"   App: {app_name}, User: {user_id}, Session: {session_id}")

    try:
        session = await session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)

        if session and session.state:
            state_dict = session.state
            app_items = {k: v for k, v in state_dict.items() if k.startswith("app:")}
            user_items = {k: v for k, v in state_dict.items() if k.startswith("user:")}
            session_items = {k: v for k, v in state_dict.items() if not k.startswith(("app:", "user:", "temp:"))}

            print(f"   📱 App-level state: {app_items}")
            print(f"   👤 User-level state: {user_items}")
            print(f"   💬 Session-level state: {session_items}")
        else:
            print("   No state data")
    except Exception as e:
        print(f"   Error: {e}")
    print("-" * 50)
