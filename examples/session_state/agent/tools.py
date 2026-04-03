# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Tools for the agent. """

from trpc_agent_sdk.agents import InvocationContext


# ===== Example 2: Modify State in tools =====
async def update_user_preference(preference: str, value: str, tool_context: InvocationContext) -> str:
    """update user preference
    Args:
        preference: str, the preference name
        value: str, the preference value
    Returns:
        str, the result
    """
    # Save user level preference
    tool_context.state[f"{preference}"] = value

    # Record operation history (session level)
    history = tool_context.state.get("preference_history", [])
    history.append(f"UPDATE:{preference}={value}")
    tool_context.state["preference_history"] = history

    return f"Preference updated: {preference} = {value}"


async def get_current_preferences(tool_context: InvocationContext) -> str:
    """get current user preference
    Args:
        None
    Returns:
        str, the result
    """
    preferences = []
    # Use to_dict() method to properly iterate through State object
    for key, value in tool_context.state.to_dict().items():
        preferences.append(f"{key}={value}")

    result = "Current user preferences:\n" + "\n".join(preferences)
    return result


async def set_state_at_different_levels(level: str, value: str, tool_context: InvocationContext) -> str:
    """Set different levels of state"""
    if level == "session":
        tool_context.state["value"] = value
        return f"Session level state set: value = {value}"
    elif level == "user":
        tool_context.state["user:value"] = value
        return f"User level state set: user:value = {value}"
    elif level == "app":
        tool_context.state["app:value"] = value
        return f"Application level state set: app:value = {value}"
    elif level == "temp":
        tool_context.state["temp:value"] = value
        return f"Temporary state set: temp:value = {value}"
    else:
        return "Please specify the correct level: session, user, app, temp"
