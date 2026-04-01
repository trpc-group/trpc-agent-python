# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the agent. """

from trpc_agent_sdk.sessions import HistoryRecord


def make_user_history_record() -> HistoryRecord:
    """Construct user history record, simulate user's previous conversation history"""
    record: dict[str, str] = {
        "What's your name?":
        "My name is Alice",
        "what is the weather like in paris?":
        "The weather in Paris is sunny with a pleasant temperature of 25 degrees Celsius. Enjoy the sunshine if you're there!",
        "Do you remember my name?":
        "It seems I don't have your name stored in my memory. Could you remind me what your name is? I can remember it for future conversations if you'd like!",
    }

    history_record = HistoryRecord()
    for query, answer in record.items():
        history_record.add_record(query, answer)
    return history_record
