# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Prompt Templates 示例：展示三种不同的 Prompt Template 用法 """

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.prompts import MessagesPlaceholder
from langchain_core.prompts import PromptTemplate

INSTRUCTION = "You are a helpful assistant with RAG knowledge. Be conversational and remember our previous exchanges."

# === 1. PromptTemplate（StringPromptTemplate）===
# 用于格式化单个字符串，通常用于简单的输入
string_prompt = PromptTemplate.from_template("请根据以下检索到的上下文回答用户的问题。\n"
                                             "上下文：{context}\n"
                                             "问题：{query}\n"
                                             "回答：")

# === 2. ChatPromptTemplate ===
# 用于格式化消息列表，由模版列表组成
chat_prompt = ChatPromptTemplate([
    ("system", "你是一个知识问答助手，请根据提供的上下文信息回答问题。如果上下文中没有相关信息，请如实说明。"),
    ("user", "上下文：{context}\n\n问题：{query}"),
])

# === 3. MessagesPlaceholder ===
# 用于在特定位置添加消息列表，适用于需要保留对话历史的场景
messages_prompt = ChatPromptTemplate([
    ("system", "你是一个知识问答助手，请根据提供的上下文信息和对话历史回答问题。"),
    MessagesPlaceholder("chat_history"),
    ("user", "上下文：{context}\n\n问题：{query}"),
])

PROMPT_TEMPLATES = {
    "string_prompt": string_prompt,
    "chat_prompt": chat_prompt,
    "messages_prompt": messages_prompt,
}
