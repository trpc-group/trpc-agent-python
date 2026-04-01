# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Tool helpers for generated graph workflow."""

{% if has_tool_functions %}
from typing import Any
{% endif %}
{% if has_any_knowledge_helpers %}
from trpc_agent_sdk.knowledge import KnowledgeFilterExpr
from trpc_agent_sdk.server.knowledge.tools import AgenticLangchainKnowledgeSearchTool
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool
{% if has_trag_knowledge_helpers %}
from trpc_agent_sdk.server.knowledge.trag_adapter import TragAuthParams
from trpc_agent_sdk.server.knowledge.trag_adapter import TragDocumentLoader
from trpc_agent_sdk.server.knowledge.trag_adapter import TragDocumentLoaderParams
from trpc_agent_sdk.server.knowledge.trag_knowledge import TragKnowledge
{% endif %}
{% if has_lingshan_knowledge_helpers %}
from trpc_agent_sdk.server.lingshan_knowledge import LingshanAuthParams
from trpc_agent_sdk.server.lingshan_knowledge import LingshanKnowledge
{% endif %}
from .config import {{ knowledge_auth_imports | join(", ") }}
{% endif %}

{% if has_any_mcp_tools %}
import os
from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import SseConnectionParams
from trpc_agent_sdk.tools import StreamableHTTPConnectionParams
{% endif %}
{% if has_any_memory_search_tools %}
from trpc_agent_sdk.tools import load_memory_tool
{% endif %}
{% if not has_any_mcp_tools and not has_any_memory_search_tools and not has_any_knowledge_helpers and not has_code_constants %}
# No tools configured in this workflow.
{% endif %}


{% for code_constant in code_constants %}
{{ code_constant.const_name }} = {{ code_constant.code_literal }}


{% endfor %}
{% if has_trag_knowledge_helpers %}
def _create_trag_knowledge(auth_params: TragAuthParams) -> TragKnowledge:
    document_loader = TragDocumentLoader(TragDocumentLoaderParams(file_paths=[]))
    return TragKnowledge(auth_params=auth_params, document_loader=document_loader)
{% endif %}
{% if has_lingshan_knowledge_helpers %}


def _create_lingshan_knowledge(auth_params: LingshanAuthParams) -> LingshanKnowledge:
    return LingshanKnowledge(auth_params=auth_params)
{% endif %}


{% for tool_func in tool_functions %}
def {{ tool_func.func_name }}() -> list[Any]:
    tools: list[Any] = []
{% for connection in tool_func.connections %}
    {{ connection.var_name }}_url = os.getenv({{ connection.url_env_name_literal }})
    if not {{ connection.var_name }}_url:
        raise ValueError("MCP server_url is empty")
    {{ connection.var_name }} = {{ connection.connection_class }}(
        url={{ connection.var_name }}_url,
{% if connection.has_headers %}
        headers={{ connection.headers_literal }},
{% endif %}
{% if connection.has_timeout %}
        timeout={{ connection.timeout_literal }},
{% endif %}
    )
{% if connection.has_allowed_tools %}
    tools.append(MCPToolset(connection_params={{ connection.var_name }}, tool_filter={{ connection.allowed_tools_literal }}))
{% else %}
    tools.append(MCPToolset(connection_params={{ connection.var_name }}))
{% endif %}
{% endfor %}
{% if tool_func.has_memory_search_tools %}
    tools.append(load_memory_tool)
{% endif %}
{% for knowledge_tool in tool_func.knowledge_tools %}
    knowledge_tool_{{ knowledge_tool.index }}_auth_params = {{ knowledge_tool.auth_params_func_name }}()
    knowledge_tool_{{ knowledge_tool.index }}_knowledge = {{ knowledge_tool.knowledge_factory_func_name }}(
        auth_params=knowledge_tool_{{ knowledge_tool.index }}_auth_params,
    )
{% if knowledge_tool.has_agentic_filter %}
    tools.append(
        AgenticLangchainKnowledgeSearchTool(
            rag=knowledge_tool_{{ knowledge_tool.index }}_knowledge,
            top_k={{ knowledge_tool.max_results_literal }},
            min_score={{ knowledge_tool.min_score_literal }},
            knowledge_filter={{ knowledge_tool.knowledge_filter_expr_literal }},
        ),
    )
{% else %}
    tools.append(
        LangchainKnowledgeSearchTool(
            rag=knowledge_tool_{{ knowledge_tool.index }}_knowledge,
            top_k={{ knowledge_tool.max_results_literal }},
            min_score={{ knowledge_tool.min_score_literal }},
            knowledge_filter={{ knowledge_tool.knowledge_filter_expr_literal }},
        ),
    )
{% endif %}
{% endfor %}
    return tools


{% endfor %}
{% for knowledge_node_tool in knowledge_node_tool_functions %}
def {{ knowledge_node_tool.func_name }}() -> LangchainKnowledgeSearchTool:
    auth_params = {{ knowledge_node_tool.auth_params_func_name }}()
    knowledge = {{ knowledge_node_tool.knowledge_factory_func_name }}(auth_params=auth_params)
    return LangchainKnowledgeSearchTool(
        rag=knowledge,
        top_k={{ knowledge_node_tool.max_results_literal }},
        min_score={{ knowledge_node_tool.min_score_literal }},
        knowledge_filter={{ knowledge_node_tool.knowledge_filter_expr_literal }},
    )


{% endfor %}
