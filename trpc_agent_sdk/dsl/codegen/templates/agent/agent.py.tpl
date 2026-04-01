# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Generated graph workflow construction."""

from trpc_agent_sdk.agents.llm_agent import LlmAgent
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.dsl.graph import END
from trpc_agent_sdk.dsl.graph import GraphAgent
from trpc_agent_sdk.dsl.graph import NodeConfig
from trpc_agent_sdk.dsl.graph import StateGraph

{% if config_imports %}
from .config import {{ config_imports | join(", ") }}
{% endif %}
{% if node_imports or node_constant_imports %}
from .nodes import {{ (node_imports + node_constant_imports) | join(", ") }}
{% endif %}
{% for route_import in route_imports %}
from .nodes import {{ route_import }}
{% endfor %}
{% if prompt_imports %}
from .prompts import {{ prompt_imports | join(", ") }}
{% endif %}
from .state import {{ state_imports | join(", ") }}
{% if tool_imports or knowledge_node_tool_imports or code_imports %}
from .tools import {{ (tool_imports + knowledge_node_tool_imports + code_imports) | join(", ") }}
{% endif %}


{% for builder in builders %}
def {{ builder.builder_name }}() -> LlmAgent:
{% if builder.generation_args %}
    generation_config = GenerateContentConfig(
{% for arg in builder.generation_args %}
        {{ arg.name }}={{ arg.value_code }},
{% endfor %}
    )
{% else %}
    generation_config = None
{% endif %}
{% if builder.has_skills %}
    skill_repository, skill_tools = {{ builder.skill_repository_and_toolset_func_name }}()
    tools = skill_tools + {{ builder.tools_expr }}
{% else %}
    tools = {{ builder.tools_expr }}
{% endif %}
    return LlmAgent(
        name={{ builder.node_name_expr }},
        description={{ builder.description_literal }},
        model={{ builder.model_func_name }}(),
        instruction={{ builder.instruction_const }},
        tools=tools,
        generate_content_config=generation_config,
{% if builder.output_model_name %}
        output_schema={{ builder.output_model_name }},
{% endif %}
{% if builder.has_skills %}
        skill_repository=skill_repository,
{% endif %}
    )


{% endfor %}
def create_agent() -> GraphAgent:
    graph = StateGraph(WorkflowState)

{% for node in start_node_defs %}
    graph.add_node(
        {{ node.const_name }},
        {{ node.func_name }},
        config=NodeConfig(name={{ node.const_name }}, description={{ node.description_literal }}),
    )
{% endfor %}
{% for node in transform_node_defs %}
    graph.add_node(
        {{ node.const_name }},
        {{ node.func_name }},
        config=NodeConfig(name={{ node.const_name }}, description={{ node.description_literal }}),
    )
{% endfor %}
{% for node in mcp_node_defs %}
    graph.add_mcp_node(
        {{ node.const_name }},
        {{ node.toolset_func_name }}(),
        selected_tool_name={{ node.selected_tool_name_literal }},
        req_src_node={{ node.req_src_node_literal }},
        config=NodeConfig(name={{ node.const_name }}, description={{ node.description_literal }}),
    )
{% endfor %}
{% for node in end_node_defs %}
    graph.add_node(
        {{ node.const_name }},
        {{ node.func_name }},
        config=NodeConfig(name={{ node.const_name }}, description={{ node.description_literal }}),
    )
{% endfor %}
{% for node in set_state_node_defs %}
    graph.add_node(
        {{ node.const_name }},
        {{ node.func_name }},
        config=NodeConfig(name={{ node.const_name }}, description={{ node.description_literal }}),
    )
{% endfor %}
{% for node in user_approval_node_defs %}
    graph.add_node(
        {{ node.const_name }},
        {{ node.func_name }},
        config=NodeConfig(name={{ node.const_name }}, description={{ node.description_literal }}),
    )
{% endfor %}
{% for node in code_node_defs %}
    graph.add_code_node(
        {{ node.const_name }},
        {{ node.code_executor_func_name }}(),
        {{ node.code_const_name }},
        {{ node.language_literal }},
        config=NodeConfig(name={{ node.const_name }}, description={{ node.description_literal }}),
    )
{% endfor %}
{% for node in knowledge_node_defs %}
    graph.add_knowledge_node(
        {{ node.const_name }},
        {{ node.query_func_name }},
        {{ node.tool_func_name }}(),
        config=NodeConfig(name={{ node.const_name }}, description={{ node.description_literal }}),
    )
{% endfor %}
{% for node in custom_node_defs %}
    graph.add_node(
        {{ node.const_name }},
        {{ node.func_name }},
        config=NodeConfig(name={{ node.const_name }}, description={{ node.description_literal }}),
    )
{% endfor %}
{% for node in agent_node_defs %}
    graph.add_agent_node(
        {{ node.const_name }},
        {{ node.builder_name }}(),
{% if node.has_input_mapper %}
        input_mapper={{ node.input_mapper_func_name }},
{% endif %}
        config=NodeConfig(name={{ node.const_name }}, description={{ node.description_literal }}),
    )
{% endfor %}

{% for edge in edges %}
    graph.add_edge({{ edge.source_expr }}, {{ edge.target_expr }})
{% endfor %}
{% for cond_edge in conditional_edges %}
    graph.add_conditional_edges({{ cond_edge.source_const }}, {{ cond_edge.route_func }})
{% endfor %}
    graph.set_entry_point({{ entry_point_const }})

    return GraphAgent(
        name={{ workflow_name_literal }},
        description={{ workflow_description_literal }},
        graph=graph.compile(),
    )


root_agent = create_agent()
