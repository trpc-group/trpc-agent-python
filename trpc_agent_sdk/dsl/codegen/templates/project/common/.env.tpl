# Generated environment variables for this workflow.
# Update the values as needed.
{% if env_entries %}
{% for entry in env_entries %}
{{ entry.name }}={{ entry.value_literal }}
{% endfor %}
{% else %}
# No environment variables are required.
{% endif %}
