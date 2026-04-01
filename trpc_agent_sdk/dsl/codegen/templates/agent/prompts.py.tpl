# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Prompt definitions for generated graph workflow."""

{% if prompts %}
{% for prompt in prompts %}
{{ prompt.const_name }} = {{ prompt.instruction_literal }}

{% endfor %}
{% else %}
# No llmagent instructions found in workflow.
{% endif %}
