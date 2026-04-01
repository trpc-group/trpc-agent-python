# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from trpc_agent_sdk.sessions import InMemorySessionService


async def print_session_state(session_service: InMemorySessionService, app_name: str, user_id: str, session_id: str,
                              title: str):
    """打印当前状态，用于调试
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

            print(f"   📱 应用级状态: {app_items}")
            print(f"   👤 用户级状态: {user_items}")
            print(f"   💬 会话级状态: {session_items}")
        else:
            print("   无状态数据")
    except Exception as e:
        print(f"   错误: {e}")
    print("-" * 50)
