# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Node helpers and routing functions for generated graph workflow."""

from typing import Any
{% if needs_end_in_route_targets %}
from trpc_agent_sdk.dsl.graph import END
{% endif %}
{% if state_key_imports %}
from trpc_agent_sdk.dsl.graph._define import (
{% for const_name in state_key_imports %}
    {{ const_name }},
{% endfor %}
)
{% endif %}
{% if has_user_approval_nodes %}
from trpc_agent_sdk.dsl.graph import interrupt
{% endif %}
from .state import WorkflowState


{% for node_const in node_constants %}
{{ node_const.const_name }} = {{ node_const.value_literal }}
{% endfor %}


{% for node in start_node_functions %}
async def {{ node.func_name }}(state: WorkflowState) -> dict[str, Any]:
    return {}


{% endfor %}
{% for mapper in agent_input_mapper_functions %}
def {{ mapper.func_name }}(state: WorkflowState) -> dict[str, Any]:
    child_state = dict(state)
{% for assignment in mapper.assignments %}
    child_state[{{ assignment.key_literal }}] = {{ assignment.source_expr }}
{% endfor %}
    return child_state


{% endfor %}
{% for node in transform_node_functions %}
async def {{ node.func_name }}(state: WorkflowState) -> dict[str, Any]:
{% if node.has_expr %}
    value = {{ node.compiled_expr_code }}
    return {
        STATE_KEY_NODE_RESPONSES: {
            {{ node.node_const_name }}: value
        }
    }
{% else %}
    return {}
{% endif %}


{% endfor %}
{% for node in set_state_node_functions %}
async def {{ node.func_name }}(state: WorkflowState) -> dict[str, Any]:
{% if node.has_assignments %}
    return {
{% for assignment in node.assignments %}
        {{ assignment.field_literal }}: {{ assignment.expr_code }},
{% endfor %}
    }
{% else %}
    return {}
{% endif %}


{% endfor %}
{% for node in end_node_functions %}
async def {{ node.func_name }}(state: WorkflowState) -> dict[str, Any]:
{% if node.has_expr %}
    value = {{ node.compiled_expr_code }}
    last_response = value

    return {
        STATE_KEY_NODE_RESPONSES: {
            {{ node.node_const_name }}: value
        },
        STATE_KEY_LAST_RESPONSE: last_response,
    }
{% else %}
    return {}
{% endif %}


{% endfor %}
{% for node in user_approval_node_functions %}
async def {{ node.func_name }}(state: WorkflowState) -> dict[str, Any]:
    desicion = interrupt({{ node.message_literal }})["desicion"]
    if desicion not in {"approve", "reject"}:
        raise ValueError("User approval input must be exactly 'approve' or 'reject'")

    return {
        STATE_KEY_NODE_RESPONSES: {
            {{ node.node_const_name }}: desicion
        }
    }


{% endfor %}
{% for node in custom_node_functions %}
async def {{ node.func_name }}(state: WorkflowState) -> dict[str, Any]:
    # TODO: implement custom node logic for {{ node.node_type_literal }}.
    return {}


{% endfor %}
{% for node in knowledge_query_functions %}
def {{ node.func_name }}(state: WorkflowState) -> str:
    return {{ node.query_expr_code }}


{% endfor %}
{% for route in route_functions %}
def {{ route.func_name }}(state: WorkflowState) -> str:
{% for case in route.cases %}
    if {{ case.predicate_expr }}:
        return {{ case.target_expr }}
{% endfor %}
{% if route.default_expr %}
    return {{ route.default_expr }}
{% else %}
    raise ValueError({{ route.error_expr }})
{% endif %}


{% endfor %}
{% for route in user_approval_route_functions %}
def {{ route.func_name }}(state: WorkflowState) -> str:
    if state[STATE_KEY_NODE_RESPONSES][{{ route.node_const_name }}] == "approve":
        return {{ route.approve_target_expr }}
    if state[STATE_KEY_NODE_RESPONSES][{{ route.node_const_name }}] == "reject":
        return {{ route.reject_target_expr }}
    raise ValueError({{ route.error_expr }})


{% endfor %}
