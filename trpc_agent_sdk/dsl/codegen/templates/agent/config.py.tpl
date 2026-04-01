# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Model configuration helpers for generated graph workflow."""

{% if uses_os_env %}
import os
{% endif %}
from typing import Any

from trpc_agent_sdk.models import OpenAIModel
{% if has_code_executor_functions %}
from trpc_agent_sdk.code_executors import BaseCodeExecutor
{% endif %}
{% if has_local_code_executor_functions %}
from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
{% endif %}
{% if has_mcp_toolset_functions %}
from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import SseConnectionParams
from trpc_agent_sdk.tools import StreamableHTTPConnectionParams
{% endif %}
{% if has_trag_knowledge_auth_functions %}
from trpc_agent_sdk.server.knowledge.trag_adapter import TragAuthParams
{% endif %}
{% if has_lingshan_knowledge_auth_functions %}
from trpc_agent_sdk.server.lingshan_knowledge import LingshanAuthParams
{% endif %}
{% if has_skill_repository_functions %}
from trpc_agent_sdk.skills import create_default_skill_repository
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills import DynamicSkillToolSet
{% endif %}
{% if has_local_skill_repository_functions %}
from trpc_agent_sdk.code_executors import create_local_workspace_runtime
{% endif %}
{% if has_pcg_skill_repository_functions or has_pcg_code_executor_functions %}
from trpc_agent_sdk.server.pcg123_code_executors import CodeExecutorConfig
from trpc_agent_sdk.server.pcg123_code_executors import Language
from trpc_agent_sdk.server.pcg123_code_executors import Pcg123CodeExecutor
{% endif %}
{% if has_pcg_skill_repository_functions %}
from trpc_agent_sdk.server.pcg123_code_executors import create_pcg123_workspace_runtime
{% endif %}


def create_openai_model(
    model_name: str | None,
    api_key: str | None,
    base_url: str | None,
    headers: dict[str, str] | None = None,
) -> OpenAIModel:
    kwargs: dict[str, Any] = {}
    if headers:
        kwargs["client_args"] = {"default_headers": headers}
    return OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        **kwargs,
    )


{% if model_functions %}
{% for model_func in model_functions %}
def {{ model_func.func_name }}() -> OpenAIModel:
    model_name = {{ model_func.model_name_expr }}
    api_key = {{ model_func.api_key_expr }}
    base_url = {{ model_func.base_url_expr }}
    return create_openai_model(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        headers={{ model_func.headers_literal }},
    )


{% endfor %}
{% else %}
# No llmagent model config found in workflow.
{% endif %}
{% if has_mcp_toolset_functions %}


{% for mcp_func in mcp_toolset_functions %}
def {{ mcp_func.func_name }}() -> MCPToolset:
    server_url = os.getenv({{ mcp_func.server_url_env_name_literal }})
    if not server_url:
        raise ValueError("MCP server_url is empty for node " + {{ mcp_func.node_id_literal }})

    connection_params = {{ mcp_func.connection_class }}(
        url=server_url,
{% if mcp_func.has_headers %}
        headers={{ mcp_func.headers_literal }},
{% endif %}
{% if mcp_func.has_timeout %}
        timeout={{ mcp_func.timeout_literal }},
{% endif %}
    )
{% if mcp_func.has_allowed_tools %}
    return MCPToolset(connection_params=connection_params, tool_filter={{ mcp_func.allowed_tools_literal }})
{% else %}
    return MCPToolset(connection_params=connection_params)
{% endif %}


{% endfor %}
{% endif %}
{% if has_knowledge_auth_functions %}


{% for auth_func in knowledge_auth_functions %}
{% if auth_func.connector_type == "trag" %}
def {{ auth_func.func_name }}() -> TragAuthParams:
    connector_type = {{ auth_func.type_expr }}
    if connector_type is None or connector_type.lower() != "trag":
        raise ValueError("Only trag knowledge connector type is supported")
    return TragAuthParams(
        namespace_code={{ auth_func.namespace_expr }},
        collection_code={{ auth_func.collection_expr }},
        api_key={{ auth_func.token_expr }},
        base_url={{ auth_func.endpoint_expr }},
        rag_code={{ auth_func.rag_code_expr }},
    )
{% elif auth_func.connector_type == "lingshan" %}
def {{ auth_func.func_name }}() -> LingshanAuthParams:
    connector_type = {{ auth_func.type_expr }}
    if connector_type is None or connector_type.lower() != "lingshan":
        raise ValueError("Only lingshan knowledge connector type is supported")
    endpoint = {{ auth_func.endpoint_expr }}
    knowledge_base_id = {{ auth_func.knowledge_base_id_expr }}
    return LingshanAuthParams(
        endpoint=endpoint,
        knowledge_base_id=knowledge_base_id,
        headers={{ auth_func.headers_literal }},
    )
{% endif %}


{% endfor %}
{% endif %}
{% if has_skill_repository_functions %}


{% for skill_func in skill_repository_functions %}
def {{ skill_func.func_name }}() -> tuple[Any, list[Any]]:
{% if skill_func.executor_type == "pcg123" %}
    secret_id = os.getenv({{ skill_func.secret_id_env_name_literal }})
    secret_key = os.getenv({{ skill_func.secret_key_env_name_literal }})
    if not secret_id or not secret_key:
        raise ValueError("pcg123 secret_id/secret_key is empty")

    executor_config = CodeExecutorConfig(
        language={{ skill_func.language_enum_expr }},
        secret_id=secret_id,
        secret_key=secret_key,
    )
    executor = Pcg123CodeExecutor.create(
        cfg=executor_config,
{% if skill_func.has_execute_timeout %}
        execute_timeout={{ skill_func.execute_timeout_literal }},
{% endif %}
{% if skill_func.has_idle_timeout %}
        idle_timeout={{ skill_func.idle_timeout_literal }},
{% endif %}
{% if skill_func.has_shared %}
        shared={{ skill_func.shared_literal }},
{% endif %}
{% if skill_func.has_interactive %}
        interactive={{ skill_func.interactive_literal }},
{% endif %}
    )
    runtime = create_pcg123_workspace_runtime(executor)
{% else %}
    work_root = {{ skill_func.work_dir_literal }} or ""
    runtime = create_local_workspace_runtime(work_root=work_root)
{% endif %}
    root_env_names = list({{ skill_func.root_env_names_literal }})
    roots: list[str] = []
    for env_name in root_env_names:
        root_value = os.getenv(env_name)
        if not root_value:
            raise ValueError("skill root is empty for env " + env_name)
        roots.append(root_value)
    repository = create_default_skill_repository(*roots, workspace_runtime=runtime)
    skill_toolset = SkillToolSet(repository=repository)
    dynamic_toolset = DynamicSkillToolSet(
        skill_repository=repository,
        only_active_skills={{ "True" if skill_func.only_active_skills else "False" }},
    )
    return repository, [skill_toolset, dynamic_toolset]


{% endfor %}
{% endif %}
{% if has_code_executor_functions %}


{% for code_executor_func in code_executor_functions %}
def {{ code_executor_func.func_name }}() -> BaseCodeExecutor:
{% if code_executor_func.executor_type == "pcg123" %}
    secret_id = os.getenv({{ code_executor_func.secret_id_env_name_literal }})
    secret_key = os.getenv({{ code_executor_func.secret_key_env_name_literal }})
    if not secret_id or not secret_key:
        raise ValueError("pcg123 secret_id/secret_key is empty")

    executor_config = CodeExecutorConfig(
        language={{ code_executor_func.language_enum_expr }},
        secret_id=secret_id,
        secret_key=secret_key,
    )
    return Pcg123CodeExecutor.create(
        cfg=executor_config,
{% if code_executor_func.has_execute_timeout %}
        execute_timeout={{ code_executor_func.execute_timeout_literal }},
{% endif %}
{% if code_executor_func.has_idle_timeout %}
        idle_timeout={{ code_executor_func.idle_timeout_literal }},
{% endif %}
{% if code_executor_func.has_shared %}
        shared={{ code_executor_func.shared_literal }},
{% endif %}
{% if code_executor_func.has_interactive %}
        interactive={{ code_executor_func.interactive_literal }},
{% endif %}
    )
{% else %}
    return UnsafeLocalCodeExecutor(
        timeout={{ code_executor_func.timeout_literal }},
        work_dir={{ code_executor_func.work_dir_literal }},
        clean_temp_files={{ code_executor_func.clean_temp_files_literal }},
    )
{% endif %}


{% endfor %}
{% endif %}
