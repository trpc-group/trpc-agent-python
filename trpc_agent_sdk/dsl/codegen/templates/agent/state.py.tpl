# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""State schema for generated graph workflow."""

from typing import {{ typing_imports | join(", ") }}
{% if pydantic_imports %}
from pydantic import {{ pydantic_imports | join(", ") }}
{% endif %}

from trpc_agent_sdk.dsl.graph import State


{% for model_block in model_blocks %}
{{ model_block }}


{% endfor %}
class WorkflowState(State):
{% if has_state_fields %}
{% for field in state_fields %}
    {{ field.name }}: {{ field.annotation }}
{% endfor %}
{% else %}
    pass
{% endif %}
