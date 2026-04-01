# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Render workflow definition into Python source files via Jinja2 templates."""

import ast
import json
import keyword
import re
from pathlib import Path
from typing import Any
from typing import Optional

from jinja2 import Environment
from jinja2 import FileSystemLoader
from jinja2 import StrictUndefined

from ..graph import STATE_KEY_AGENT_CALLBACKS
from ..graph import STATE_KEY_CURRENT_NODE_ID
from ..graph import STATE_KEY_EXEC_CONTEXT
from ..graph import STATE_KEY_LAST_RESPONSE
from ..graph import STATE_KEY_LAST_RESPONSE_ID
from ..graph import STATE_KEY_LAST_TOOL_RESPONSE
from ..graph import STATE_KEY_MESSAGES
from ..graph import STATE_KEY_METADATA
from ..graph import STATE_KEY_MODEL_CALLBACKS
from ..graph import STATE_KEY_NODE_CALLBACKS
from ..graph import STATE_KEY_NODE_RESPONSES
from ..graph import STATE_KEY_ONE_SHOT_MESSAGES
from ..graph import STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE
from ..graph import STATE_KEY_SESSION
from ..graph import STATE_KEY_STEP_NUMBER
from ..graph import STATE_KEY_TOOL_CALLBACKS
from ..graph import STATE_KEY_USER_INPUT
from ._workflow import WorkflowDefinition

_SUPPORTED_SERVICE_MODES = frozenset({"http", "a2a", "agui"})
_SUPPORTED_KNOWLEDGE_CONNECTOR_TYPES = frozenset({"trag", "lingshan"})
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TEMPLATE_EXPR_PATTERN = re.compile(r"\{\{\s*(.*?)\s*\}\}")
_STATE_KEY_CONST_BY_VALUE = {
    STATE_KEY_USER_INPUT: "STATE_KEY_USER_INPUT",
    STATE_KEY_MESSAGES: "STATE_KEY_MESSAGES",
    STATE_KEY_LAST_RESPONSE: "STATE_KEY_LAST_RESPONSE",
    STATE_KEY_LAST_RESPONSE_ID: "STATE_KEY_LAST_RESPONSE_ID",
    STATE_KEY_LAST_TOOL_RESPONSE: "STATE_KEY_LAST_TOOL_RESPONSE",
    STATE_KEY_NODE_RESPONSES: "STATE_KEY_NODE_RESPONSES",
    STATE_KEY_ONE_SHOT_MESSAGES: "STATE_KEY_ONE_SHOT_MESSAGES",
    STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE: "STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE",
    STATE_KEY_METADATA: "STATE_KEY_METADATA",
    STATE_KEY_SESSION: "STATE_KEY_SESSION",
    STATE_KEY_CURRENT_NODE_ID: "STATE_KEY_CURRENT_NODE_ID",
    STATE_KEY_EXEC_CONTEXT: "STATE_KEY_EXEC_CONTEXT",
    STATE_KEY_TOOL_CALLBACKS: "STATE_KEY_TOOL_CALLBACKS",
    STATE_KEY_MODEL_CALLBACKS: "STATE_KEY_MODEL_CALLBACKS",
    STATE_KEY_AGENT_CALLBACKS: "STATE_KEY_AGENT_CALLBACKS",
    STATE_KEY_NODE_CALLBACKS: "STATE_KEY_NODE_CALLBACKS",
    STATE_KEY_STEP_NUMBER: "STATE_KEY_STEP_NUMBER",
}


def _to_python_identifier(value: str, *, upper: bool = False) -> str:
    text = re.sub(r"[^a-zA-Z0-9_]", "_", value.strip())
    text = re.sub(r"_+", "_", text).strip("_")
    if text == "":
        text = "value"
    if text[0].isdigit():
        text = f"n_{text}"
    if keyword.iskeyword(text):
        text = f"{text}_value"
    return text.upper() if upper else text.lower()


def _to_pascal(value: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9]+", " ", value.strip())
    parts = [part for part in raw.split(" ") if part]
    if not parts:
        return "Generated"
    result = "".join(part[:1].upper() + part[1:] for part in parts)
    if result[0].isdigit():
        result = f"N{result}"
    return result


class _Renderer:

    def __init__(self, workflow: WorkflowDefinition, service_mode: Optional[str]):
        normalized_mode: Optional[str]
        if service_mode is None:
            normalized_mode = None
        else:
            normalized_mode = service_mode.strip().lower()
            if normalized_mode not in _SUPPORTED_SERVICE_MODES:
                raise ValueError(f"Unsupported service mode: {service_mode!r}. Supported: "
                                 f"{', '.join(sorted(_SUPPORTED_SERVICE_MODES))}")

        self.workflow = workflow
        self.service_mode = normalized_mode
        self.agent_nodes = [node for node in workflow.nodes if node.node_type == "builtin.llmagent"]
        self.start_nodes = [node for node in workflow.nodes if node.node_type == "builtin.start"]
        self.end_nodes = [node for node in workflow.nodes if node.node_type == "builtin.end"]
        self.transform_nodes = [node for node in workflow.nodes if node.node_type == "builtin.transform"]
        self.code_nodes = [node for node in workflow.nodes if node.node_type == "builtin.code"]
        self.mcp_nodes = [node for node in workflow.nodes if node.node_type == "builtin.mcp"]
        self.knowledge_nodes = [node for node in workflow.nodes if node.node_type == "builtin.knowledge_search"]
        self.supported_knowledge_nodes = [
            node for node in self.knowledge_nodes if node.knowledge_config is not None
            and node.knowledge_config.connector.connector_type in _SUPPORTED_KNOWLEDGE_CONNECTOR_TYPES
        ]
        self.unsupported_knowledge_nodes = [
            node for node in self.knowledge_nodes if node not in self.supported_knowledge_nodes
        ]
        self.unsupported_knowledge_node_ids = {node.node_id for node in self.unsupported_knowledge_nodes}
        self.set_state_nodes = [node for node in workflow.nodes if node.node_type == "builtin.set_state"]
        self.user_approval_nodes = [node for node in workflow.nodes if node.node_type == "builtin.user_approval"]
        self.custom_nodes = [node for node in workflow.nodes if node.node_type.startswith("custom.")]

        self.node_const_names: dict[str, str] = {}
        self.node_func_names: dict[str, str] = {}
        self.knowledge_query_func_names: dict[str, str] = {}
        self.knowledge_node_tool_func_names: dict[str, str] = {}
        self.knowledge_node_auth_func_names: dict[str, str] = {}
        self.route_func_names: dict[str, str] = {}
        self.output_model_names: dict[str, str] = {}
        self.instruction_const_names: dict[str, str] = {}
        self.model_func_names: dict[str, str] = {}
        self.tool_func_names: dict[str, str] = {}
        self.mcp_toolset_func_names: dict[str, str] = {}
        self.agent_builder_names: dict[str, str] = {}
        self.agent_input_mapper_func_names: dict[str, str] = {}
        self._agent_instruction_bindings_cache: Optional[dict[str, dict[str, Any]]] = None
        self.skill_repository_and_toolset_func_names: dict[str, str] = {}
        self.code_executor_func_names: dict[str, str] = {}
        self.model_env_names_by_node: dict[str, dict[str, str]] = {}
        self.mcp_env_name_by_node: dict[str, str] = {}
        self.agent_mcp_env_name_by_tool: dict[tuple[str, int], str] = {}
        self.knowledge_env_names_by_auth_func: dict[str, dict[str, str]] = {}
        self.skill_pcg_env_names_by_node: dict[str, dict[str, str]] = {}
        self.skill_root_env_names_by_node: dict[str, tuple[str, ...]] = {}
        self.code_pcg_env_names_by_node: dict[str, dict[str, str]] = {}
        self.dotenv_entries: list[dict[str, str]] = []

        self._uses_literal = False
        self._uses_field = False
        self._uses_optional_field = False

        templates_dir = Path(__file__).resolve().parent / "templates"
        self.template_env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=False,
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        self._assign_names()
        self._assign_env_names()

    @staticmethod
    def _py_string(value: str) -> str:
        return repr(value)

    def _render_template(self, template_name: str, **context: Any) -> str:
        template = self.template_env.get_template(template_name)
        return template.render(**context)

    @staticmethod
    def _extract_node_symbol(const_name: str) -> str:
        prefix = "NODE_ID_"
        if const_name.startswith(prefix):
            return const_name[len(prefix):]
        return const_name

    def _node_symbol_upper(self, node_id: str) -> str:
        const_name = self.node_const_names.get(node_id)
        if const_name is None:
            raise ValueError(f"Unknown node id {node_id!r}")
        return self._extract_node_symbol(const_name)

    def _node_symbol_lower(self, node_id: str) -> str:
        return _to_python_identifier(self._node_symbol_upper(node_id))

    def _node_symbol_pascal(self, node_id: str) -> str:
        return _to_pascal(self._node_symbol_lower(node_id))

    def _assign_names(self) -> None:
        seen_consts: set[str] = set()
        node_type_counts: dict[str, int] = {}
        has_start_node = False
        for node in self.workflow.nodes:
            if node.node_type == "builtin.start":
                if has_start_node:
                    raise ValueError("workflow contains multiple builtin.start nodes")
                has_start_node = True
                const_name = "NODE_ID_START"
            else:
                node_type_suffix = node.node_type
                if node.node_type.startswith("builtin."):
                    node_type_suffix = node.node_type.split(".", 1)[1]
                node_type_key = _to_python_identifier(node_type_suffix, upper=True)
                node_type_counts[node_type_key] = node_type_counts.get(node_type_key, 0) + 1
                const_name = f"NODE_ID_{node_type_key}{node_type_counts[node_type_key]}"

            if const_name in seen_consts:
                raise ValueError(f"generated duplicated node constant name {const_name!r}")
            seen_consts.add(const_name)
            self.node_const_names[node.node_id] = const_name

        seen_node_funcs: set[str] = set()
        for node in self.workflow.nodes:
            if node.node_type in {"builtin.llmagent", "builtin.code"}:
                continue
            if node.node_type == "builtin.knowledge_search" and node.node_id not in self.unsupported_knowledge_node_ids:
                continue
            node_symbol = self._node_symbol_lower(node.node_id)
            base_func = f"node_{node_symbol}"
            func_name = base_func
            suffix = 2
            while func_name in seen_node_funcs:
                func_name = f"{base_func}_{suffix}"
                suffix += 1
            seen_node_funcs.add(func_name)
            self.node_func_names[node.node_id] = func_name

        seen_knowledge_query_funcs: set[str] = set()
        seen_knowledge_tool_funcs: set[str] = set()
        seen_knowledge_auth_funcs: set[str] = set()
        for knowledge_index, node in enumerate(self.supported_knowledge_nodes, start=1):
            node_symbol = self._node_symbol_lower(node.node_id)
            cfg = node.knowledge_config
            if cfg is None:
                raise ValueError(f"builtin.knowledge_search[{node.node_id}] has no parsed config")
            connector_type = cfg.connector.connector_type

            base_func = f"resolve_query_{node_symbol}"
            func_name = base_func
            suffix = 2
            while func_name in seen_knowledge_query_funcs:
                func_name = f"{base_func}_{suffix}"
                suffix += 1
            seen_knowledge_query_funcs.add(func_name)
            self.knowledge_query_func_names[node.node_id] = func_name

            base_tool_func = f"create_knowledge_tool_{node_symbol}"
            tool_func_name = base_tool_func
            suffix = 2
            while tool_func_name in seen_knowledge_tool_funcs:
                tool_func_name = f"{base_tool_func}_{suffix}"
                suffix += 1
            seen_knowledge_tool_funcs.add(tool_func_name)
            self.knowledge_node_tool_func_names[node.node_id] = tool_func_name

            base_auth_func = self._build_knowledge_node_auth_func_name(knowledge_index, connector_type)
            auth_func_name = base_auth_func
            suffix = 2
            while auth_func_name in seen_knowledge_auth_funcs:
                auth_func_name = f"{base_auth_func}_{suffix}"
                suffix += 1
            seen_knowledge_auth_funcs.add(auth_func_name)
            self.knowledge_node_auth_func_names[node.node_id] = auth_func_name

        seen_mcp_toolset_funcs: set[str] = set()
        for node in self.mcp_nodes:
            node_symbol = self._node_symbol_lower(node.node_id)
            base_toolset_func = f"create_mcp_toolset_{node_symbol}"
            toolset_func_name = base_toolset_func
            suffix = 2
            while toolset_func_name in seen_mcp_toolset_funcs:
                toolset_func_name = f"{base_toolset_func}_{suffix}"
                suffix += 1
            seen_mcp_toolset_funcs.add(toolset_func_name)
            self.mcp_toolset_func_names[node.node_id] = toolset_func_name

        route_func_index = 1
        for index, edge in enumerate(self.workflow.conditional_edges):
            route_key = edge.edge_id or f"{edge.from_node}:{index}"
            self.route_func_names[route_key] = f"route_func{route_func_index}"
            route_func_index += 1

        for node in self.user_approval_nodes:
            route_key = f"user_approval:{node.node_id}"
            self.route_func_names[route_key] = f"route_func{route_func_index}"
            route_func_index += 1

        seen_models: set[str] = set()
        for node in self.agent_nodes:
            cfg = node.llm_config
            if cfg is None or cfg.output_mode != "json" or cfg.output_schema is None:
                continue
            base_model_name = f"{self._node_symbol_pascal(node.node_id)}OutputModel"
            model_name = base_model_name
            suffix = 2
            while model_name in seen_models:
                model_name = f"{base_model_name}{suffix}"
                suffix += 1
            seen_models.add(model_name)
            self.output_model_names[node.node_id] = model_name

        for node in self.agent_nodes:
            node_symbol_upper = self._node_symbol_upper(node.node_id)
            node_symbol_lower = self._node_symbol_lower(node.node_id)
            self.instruction_const_names[node.node_id] = f"{node_symbol_upper}_INSTRUCTION"
            self.model_func_names[node.node_id] = f"create_model_{node_symbol_lower}"
            if node_symbol_lower.endswith("_agent"):
                self.agent_builder_names[node.node_id] = f"_create_{node_symbol_lower}"
            else:
                self.agent_builder_names[node.node_id] = f"_create_{node_symbol_lower}_agent"
            if node.llm_config and (node.llm_config.mcp_tools or node.llm_config.knowledge_search_tools
                                    or node.llm_config.memory_search_tools):
                self.tool_func_names[node.node_id] = f"create_tools_{node_symbol_lower}"
            if node.llm_config and node.llm_config.skills:
                self.skill_repository_and_toolset_func_names[node.node_id] = (
                    f"create_skill_repository_and_tools_{node_symbol_lower}")

        seen_code_executor_funcs: set[str] = set()
        for node in self.code_nodes:
            node_symbol = self._node_symbol_lower(node.node_id)
            base_func = f"create_code_executor_{node_symbol}"
            func_name = base_func
            suffix = 2
            while func_name in seen_code_executor_funcs:
                func_name = f"{base_func}_{suffix}"
                suffix += 1
            seen_code_executor_funcs.add(func_name)
            self.code_executor_func_names[node.node_id] = func_name

        seen_mapper_funcs: set[str] = set()
        for node in self.agent_nodes:
            node_symbol = self._node_symbol_lower(node.node_id)
            base_func = f"map_input_{node_symbol}"
            func_name = base_func
            suffix = 2
            while func_name in seen_mapper_funcs:
                func_name = f"{base_func}_{suffix}"
                suffix += 1
            seen_mapper_funcs.add(func_name)
            self.agent_input_mapper_func_names[node.node_id] = func_name

    def _schema_to_annotation(self, schema: dict[str, Any]) -> str:
        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and enum_values:
            if all(isinstance(item, (str, int, float, bool)) for item in enum_values):
                self._uses_literal = True
                return f"Literal[{', '.join(repr(item) for item in enum_values)}]"

        schema_type = schema.get("type")
        if isinstance(schema_type, list):
            non_null = [item for item in schema_type if item != "null"]
            if len(non_null) == 1:
                schema_type = non_null[0]
            elif non_null:
                schema_type = non_null[0]
            else:
                schema_type = None

        if schema_type == "string":
            return "str"
        if schema_type == "integer":
            return "int"
        if schema_type == "number":
            return "float"
        if schema_type == "boolean":
            return "bool"
        if schema_type == "array":
            items = schema.get("items")
            if isinstance(items, dict):
                return f"list[{self._schema_to_annotation(items)}]"
            return "list[Any]"
        if schema_type == "object":
            return "dict[str, Any]"
        return "Any"

    def _render_output_model_class(self, class_name: str, schema: dict[str, Any]) -> str:
        lines: list[str] = [f"class {class_name}(BaseModel):"]

        if schema.get("additionalProperties") is False:
            lines.append("    model_config = ConfigDict(extra=\"forbid\")")

        properties = schema.get("properties", {})
        if not isinstance(properties, dict) or not properties:
            self._uses_field = True
            lines.append("    payload: dict[str, Any] = Field(default_factory=dict)")
            return "\n".join(lines)

        required_raw = schema.get("required", [])
        required_set = set(required_raw) if isinstance(required_raw, list) else set()

        for raw_key in sorted(properties.keys()):
            prop_schema = properties.get(raw_key)
            annotation = "Any"
            if isinstance(prop_schema, dict):
                annotation = self._schema_to_annotation(prop_schema)

            py_key = _to_python_identifier(raw_key)
            needs_alias = py_key != raw_key
            if needs_alias:
                self._uses_field = True

            if raw_key in required_set:
                if needs_alias:
                    lines.append(f"    {py_key}: {annotation} = Field(alias={raw_key!r})")
                else:
                    lines.append(f"    {py_key}: {annotation}")
                continue

            self._uses_optional_field = True
            optional_annotation = f"Optional[{annotation}]"
            if needs_alias:
                lines.append(f"    {py_key}: {optional_annotation} = Field(default=None, alias={raw_key!r})")
            else:
                lines.append(f"    {py_key}: {optional_annotation} = None")

        return "\n".join(lines)

    @staticmethod
    def _build_knowledge_env_names(index: int) -> dict[str, str]:
        return {
            "type": f"KNOWLEDGE{index}_TYPE",
            "endpoint": f"KNOWLEDGE{index}_ENDPOINT",
            "token": f"KNOWLEDGE{index}_TOKEN",
            "rag_code": f"KNOWLEDGE{index}_RAG_CODE",
            "namespace": f"KNOWLEDGE{index}_NAMESPACE",
            "collection": f"KNOWLEDGE{index}_COLLECTION",
            "knowledge_base_id": f"KNOWLEDGE{index}_KNOWLEDGE_BASE_ID",
        }

    @staticmethod
    def _build_pcg_env_names(index: int) -> dict[str, str]:
        return {
            "secret_id": f"PCG123_SECRET_ID{index}",
            "secret_key": f"PCG123_SECRET_KEY{index}",
        }

    @staticmethod
    def _pcg_language_enum_expr(language: str) -> str:
        normalized = language.strip().lower()
        if normalized == "python3.8":
            return "Language.PYTHON38"
        if normalized == "python3.9":
            return "Language.PYTHON39"
        if normalized == "python3.10":
            return "Language.PYTHON310"
        raise ValueError(f"Unsupported pcg123 language: {language!r}")

    def _append_dotenv_entry(self, name: str, value: Optional[str]) -> None:
        if value is None:
            return
        text = value.strip()
        if text == "":
            return
        self.dotenv_entries.append({
            "name": name,
            "value": text,
        })

    def _assign_env_names(self) -> None:
        model_index = 1
        mcp_index = 1
        knowledge_index = 1
        pcg_index = 1
        skill_index = 1

        for node in self.workflow.nodes:
            if node.node_type == "builtin.llmagent":
                cfg = node.llm_config
                if cfg is None:
                    continue

                model_env_names = {
                    "model_name": f"MODEL{model_index}_NAME",
                    "api_key": f"MODEL{model_index}_API_KEY",
                    "base_url": f"MODEL{model_index}_BASE_URL",
                }
                self.model_env_names_by_node[node.node_id] = model_env_names
                self._append_dotenv_entry(model_env_names["model_name"], cfg.model_spec.model_name)
                self._append_dotenv_entry(model_env_names["api_key"], cfg.model_spec.api_key)
                self._append_dotenv_entry(model_env_names["base_url"], cfg.model_spec.base_url)
                model_index += 1

                for tool_index, mcp_tool in enumerate(cfg.mcp_tools, start=1):
                    mcp_env_name = f"MCP{mcp_index}_SERVER_URL"
                    self.agent_mcp_env_name_by_tool[(node.node_id, tool_index)] = mcp_env_name
                    self._append_dotenv_entry(mcp_env_name, mcp_tool.server_url)
                    mcp_index += 1

                for tool_index, knowledge_tool in enumerate(cfg.knowledge_search_tools, start=1):
                    connector = knowledge_tool.connector
                    if connector.connector_type not in _SUPPORTED_KNOWLEDGE_CONNECTOR_TYPES:
                        continue
                    knowledge_env_names = self._build_knowledge_env_names(knowledge_index)
                    auth_func_name = self._build_knowledge_auth_func_name(
                        node.node_id,
                        tool_index,
                        connector.connector_type,
                    )
                    self.knowledge_env_names_by_auth_func[auth_func_name] = knowledge_env_names
                    self._append_dotenv_entry(knowledge_env_names["type"], connector.connector_type)
                    self._append_dotenv_entry(knowledge_env_names["endpoint"], connector.endpoint)
                    if connector.connector_type == "trag":
                        self._append_dotenv_entry(knowledge_env_names["token"], connector.token)
                        self._append_dotenv_entry(knowledge_env_names["rag_code"], connector.rag_code)
                        self._append_dotenv_entry(knowledge_env_names["namespace"], connector.namespace)
                        self._append_dotenv_entry(knowledge_env_names["collection"], connector.collection)
                    else:
                        self._append_dotenv_entry(knowledge_env_names["knowledge_base_id"], connector.knowledge_base_id)
                    knowledge_index += 1

                if cfg.executor is not None and cfg.executor.type == "pcg123":
                    pcg_env_names = self._build_pcg_env_names(pcg_index)
                    self.skill_pcg_env_names_by_node[node.node_id] = pcg_env_names
                    self._append_dotenv_entry(pcg_env_names["secret_id"], cfg.executor.secret_id)
                    self._append_dotenv_entry(pcg_env_names["secret_key"], cfg.executor.secret_key)
                    pcg_index += 1

                if cfg.skills is not None:
                    root_env_names: list[str] = []
                    for root_index, root_value in enumerate(cfg.skills.roots, start=1):
                        env_name = f"SKILL{skill_index}_ROOT{root_index}"
                        root_env_names.append(env_name)
                        self._append_dotenv_entry(env_name, root_value)
                    self.skill_root_env_names_by_node[node.node_id] = tuple(root_env_names)
                    skill_index += 1
                continue

            if node.node_type == "builtin.mcp":
                cfg = node.mcp_config
                if cfg is None:
                    continue
                mcp_env_name = f"MCP{mcp_index}_SERVER_URL"
                self.mcp_env_name_by_node[node.node_id] = mcp_env_name
                self._append_dotenv_entry(mcp_env_name, cfg.mcp.server_url)
                mcp_index += 1
                continue

            if node.node_type == "builtin.knowledge_search":
                cfg = node.knowledge_config
                if cfg is None or node.node_id in self.unsupported_knowledge_node_ids:
                    continue
                knowledge_env_names = self._build_knowledge_env_names(knowledge_index)
                auth_func_name = self.knowledge_node_auth_func_names[node.node_id]
                self.knowledge_env_names_by_auth_func[auth_func_name] = knowledge_env_names
                connector = cfg.connector
                self._append_dotenv_entry(knowledge_env_names["type"], connector.connector_type)
                self._append_dotenv_entry(knowledge_env_names["endpoint"], connector.endpoint)
                if connector.connector_type == "trag":
                    self._append_dotenv_entry(knowledge_env_names["token"], connector.token)
                    self._append_dotenv_entry(knowledge_env_names["rag_code"], connector.rag_code)
                    self._append_dotenv_entry(knowledge_env_names["namespace"], connector.namespace)
                    self._append_dotenv_entry(knowledge_env_names["collection"], connector.collection)
                else:
                    self._append_dotenv_entry(knowledge_env_names["knowledge_base_id"], connector.knowledge_base_id)
                knowledge_index += 1
                continue

            if node.node_type == "builtin.code":
                cfg = node.code_config
                if cfg is None:
                    continue
                if cfg.executor.type == "pcg123":
                    pcg_env_names = self._build_pcg_env_names(pcg_index)
                    self.code_pcg_env_names_by_node[node.node_id] = pcg_env_names
                    self._append_dotenv_entry(pcg_env_names["secret_id"], cfg.executor.secret_id)
                    self._append_dotenv_entry(pcg_env_names["secret_key"], cfg.executor.secret_key)
                    pcg_index += 1

    @staticmethod
    def _dotenv_quote(value: str) -> str:
        escaped = value.replace("\\", "\\\\")
        escaped = escaped.replace('"', '\\"')
        escaped = escaped.replace("\n", "\\n")
        return f'"{escaped}"'

    def _build_knowledge_node_auth_func_name(self, index: int, connector_type: str) -> str:
        if connector_type in {"trag", "lingshan"}:
            return f"create_{connector_type}_knowledge{index}_auth"
        raise ValueError(
            f"knowledge connector type {connector_type!r} is not supported yet (supported: 'trag', 'lingshan')")

    def _build_knowledge_auth_func_name(self, node_id: str, index: int, connector_type: str) -> str:
        node_suffix = self._node_symbol_lower(node_id)
        if connector_type in {"trag", "lingshan"}:
            return f"create_{node_suffix}_{connector_type}_auth{index}"
        raise ValueError(
            f"knowledge connector type {connector_type!r} is not supported yet (supported: 'trag', 'lingshan')")

    @staticmethod
    def _read_string_literal(expression: str, start: int) -> tuple[str, int]:
        quote = expression[start]
        index = start + 1
        length = len(expression)
        while index < length:
            char = expression[index]
            if char == "\\":
                index += 2
                continue
            if char == quote:
                return expression[start:index + 1], index + 1
            index += 1
        raise ValueError("Unterminated string literal in expression")

    @staticmethod
    def _read_dotted_identifier(expression: str, start: int) -> tuple[list[str], int]:
        segments: list[str] = []
        index = start
        while True:
            match = _IDENTIFIER_PATTERN.match(expression, index)
            if match is None:
                raise ValueError("Invalid identifier in expression")
            segments.append(match.group(0))
            index = match.end()
            if index < len(expression) and expression[index] == ".":
                index += 1
                continue
            break
        return segments, index

    @staticmethod
    def _state_key_expr(key: str) -> str:
        const_name = _STATE_KEY_CONST_BY_VALUE.get(key)
        if const_name is not None:
            return const_name
        return repr(key)

    @staticmethod
    def _build_state_access_expression(segments: list[str | int]) -> str:
        if len(segments) < 2:
            raise ValueError("state reference must include at least one field")
        expression = "state"
        for index, segment in enumerate(segments[1:], start=1):
            if isinstance(segment, int):
                expression += f"[{segment}]"
            else:
                if index == 1:
                    expression += f"[{_Renderer._state_key_expr(segment)}]"
                else:
                    expression += f"[{segment!r}]"
        return expression

    @staticmethod
    def _skip_spaces(expression: str, index: int) -> int:
        length = len(expression)
        while index < length and expression[index].isspace():
            index += 1
        return index

    @staticmethod
    def _read_reference_chain(expression: str, start: int) -> tuple[list[str | int], int]:
        first_match = _IDENTIFIER_PATTERN.match(expression, start)
        if first_match is None:
            raise ValueError("Invalid identifier in expression")

        segments: list[str | int] = [first_match.group(0)]
        index = first_match.end()
        length = len(expression)
        while index < length:
            char = expression[index]
            if char == ".":
                index += 1
                next_match = _IDENTIFIER_PATTERN.match(expression, index)
                if next_match is None:
                    raise ValueError("Invalid identifier in expression")
                segments.append(next_match.group(0))
                index = next_match.end()
                continue

            if char == "[":
                index += 1
                index = _Renderer._skip_spaces(expression, index)
                number_match = re.match(r"[0-9]+", expression[index:])
                if number_match is not None:
                    segments.append(int(number_match.group(0)))
                    index += len(number_match.group(0))
                elif index < length and expression[index] in {"'", '"'}:
                    literal, next_index = _Renderer._read_string_literal(expression, index)
                    try:
                        key = ast.literal_eval(literal)
                    except (SyntaxError, ValueError) as e:
                        raise ValueError("Invalid string index access in expression") from e
                    if not isinstance(key, str):
                        raise ValueError("Only string and numeric index access are supported in expression")
                    segments.append(key)
                    index = next_index
                else:
                    raise ValueError("Only string and numeric index access are supported in expression")
                index = _Renderer._skip_spaces(expression, index)
                if index >= length or expression[index] != "]":
                    raise ValueError("Unterminated index access in expression")
                index += 1
                continue

            break
        return segments, index

    @staticmethod
    def _read_parenthesized_expression(
        expression: str,
        open_paren_index: int,
        *,
        expr_path: str,
        function_name: str,
    ) -> tuple[str, int]:
        if open_paren_index >= len(expression) or expression[open_paren_index] != "(":
            raise ValueError(f"{expr_path} has invalid {function_name}() call syntax")

        depth = 0
        index = open_paren_index
        length = len(expression)
        while index < length:
            char = expression[index]
            if char in {"'", '"'}:
                _, index = _Renderer._read_string_literal(expression, index)
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    inner = expression[open_paren_index + 1:index]
                    return inner, index + 1
            index += 1

        raise ValueError(f"{expr_path} has unterminated {function_name}() call")

    def _build_index_access_expression(self, base_expr: str, segments: list[str | int]) -> str:
        expression = base_expr
        for segment in segments:
            if isinstance(segment, int):
                expression += f"[{segment}]"
            else:
                expression += f"[{self._py_string(segment)}]"
        return expression

    def _compile_reference_chain_to_python(
        self,
        segments: list[str | int],
        *,
        expr_path: str,
        input_source_node: str,
    ) -> tuple[str, bool]:
        if not segments:
            raise ValueError(f"{expr_path} contains an empty reference")
        root = segments[0]
        if not isinstance(root, str):
            raise ValueError(f"{expr_path} has an invalid reference root")

        if root == "state":
            if len(segments) == 1:
                return "state", False
            return self._build_state_access_expression(segments), False

        if root == "input":
            if len(segments) < 2:
                raise ValueError(f"{expr_path} supports only input.output_parsed/output_text")
            output_kind = segments[1]
            if not isinstance(output_kind, str) or output_kind not in {"output_parsed", "output_text"}:
                raise ValueError(f"{expr_path} supports only input.output_parsed/output_text")
            if input_source_node == "":
                raise ValueError(f"{expr_path} uses input.output_parsed/input.output_text but "
                                 "node does not have exactly one upstream node")

            input_source_const = self._node_const_expression(input_source_node, expr_path=expr_path)
            base_expr = f"state[STATE_KEY_NODE_RESPONSES][{input_source_const}]"
            if output_kind == "output_text" and len(segments) > 2:
                raise ValueError(f"{expr_path} input.output_text cannot be dereferenced further")
            if output_kind == "output_text":
                return f"str({base_expr})", True
            return self._build_index_access_expression(base_expr, segments[2:]), True

        if root == "nodes":
            if len(segments) < 2:
                raise ValueError(f"{expr_path} supports nodes.<id> or nodes.<id>.output_parsed/output_text")
            node_id = segments[1]
            if not isinstance(node_id, str):
                raise ValueError(f"{expr_path} has invalid nodes.<id> reference")
            node_id_const = self._node_const_expression(node_id, expr_path=expr_path)
            if len(segments) == 2:
                return f"state[STATE_KEY_NODE_RESPONSES][{node_id_const}]", True

            output_kind = segments[2]
            if not isinstance(output_kind, str) or output_kind not in {"output_parsed", "output_text"}:
                raise ValueError(f"{expr_path} supports nodes.<id> or nodes.<id>.output_parsed/output_text")

            base_expr = f"state[STATE_KEY_NODE_RESPONSES][{node_id_const}]"
            if output_kind == "output_text" and len(segments) > 3:
                raise ValueError(f"{expr_path} nodes.<id>.output_text cannot be dereferenced further")
            if output_kind == "output_text":
                return f"str({base_expr})", True
            return self._build_index_access_expression(base_expr, segments[3:]), True

        dotted = ".".join(str(segment) for segment in segments)
        raise ValueError(f"{expr_path} has unsupported reference {dotted!r}; "
                         "expression supports state.*, input.output_parsed/output_text, "
                         "and nodes.<id>.output_parsed/output_text")

    def _parse_reference(self, reference: str, *, expr_path: str) -> list[str | int]:
        ref = reference.strip()
        if ref == "":
            raise ValueError(f"{expr_path} contains empty reference")
        segments, index = self._read_reference_chain(ref, 0)
        index = self._skip_spaces(ref, index)
        if index != len(ref):
            raise ValueError(f"{expr_path} has invalid reference syntax: {reference!r}")
        return segments

    def _build_placeholder_key_name(self, segments: list[str | int]) -> str:
        if not segments:
            return "value"

        root = segments[0] if isinstance(segments[0], str) else ""
        key_segments: list[str] = []
        if root == "state":
            if len(segments) == 1:
                return "state"
            key_segments = [str(segment) for segment in segments[1:]]
        elif root == "input":
            if len(segments) >= 3:
                key_segments = [str(segment) for segment in segments[2:]]
            elif len(segments) >= 2:
                key_segments = [f"input_{segments[1]}"]
            else:
                key_segments = ["input_value"]
        elif root == "nodes":
            key_segments = [str(segment) for segment in segments[1:]]
        else:
            key_segments = [str(segment) for segment in segments]

        if not key_segments:
            key_segments = ["value"]
        return _to_python_identifier("_".join(key_segments))

    @staticmethod
    def _is_wrapped_by_parentheses(expression: str) -> bool:
        if not expression.startswith("(") or not expression.endswith(")"):
            return False
        depth_paren = 0
        depth_bracket = 0
        depth_brace = 0
        index = 0
        length = len(expression)
        while index < length:
            char = expression[index]
            if char in {"'", '"'}:
                _, index = _Renderer._read_string_literal(expression, index)
                continue
            if char == "(":
                depth_paren += 1
            elif char == ")":
                depth_paren -= 1
                if depth_paren == 0 and index != length - 1:
                    return False
            elif char == "[":
                depth_bracket += 1
            elif char == "]":
                depth_bracket -= 1
            elif char == "{":
                depth_brace += 1
            elif char == "}":
                depth_brace -= 1
            index += 1
        return depth_paren == 0 and depth_bracket == 0 and depth_brace == 0

    def _find_top_level_ternary_question(self, expression: str) -> int:
        depth_paren = 0
        depth_bracket = 0
        depth_brace = 0
        index = 0
        length = len(expression)
        while index < length:
            char = expression[index]
            if char in {"'", '"'}:
                _, index = self._read_string_literal(expression, index)
                continue
            if char == "(":
                depth_paren += 1
            elif char == ")":
                depth_paren -= 1
            elif char == "[":
                depth_bracket += 1
            elif char == "]":
                depth_bracket -= 1
            elif char == "{":
                depth_brace += 1
            elif char == "}":
                depth_brace -= 1
            elif char == "?" and depth_paren == 0 and depth_bracket == 0 and depth_brace == 0:
                return index
            index += 1
        return -1

    def _find_matching_ternary_colon(self, expression: str, question_index: int, expr_path: str) -> int:
        depth_paren = 0
        depth_bracket = 0
        depth_brace = 0
        ternary_depth = 0
        index = question_index + 1
        length = len(expression)
        while index < length:
            char = expression[index]
            if char in {"'", '"'}:
                _, index = self._read_string_literal(expression, index)
                continue
            if char == "(":
                depth_paren += 1
            elif char == ")":
                depth_paren -= 1
            elif char == "[":
                depth_bracket += 1
            elif char == "]":
                depth_bracket -= 1
            elif char == "{":
                depth_brace += 1
            elif char == "}":
                depth_brace -= 1
            elif char == "?" and depth_paren == 0 and depth_bracket == 0 and depth_brace == 0:
                ternary_depth += 1
            elif char == ":" and depth_paren == 0 and depth_bracket == 0 and depth_brace == 0:
                if ternary_depth == 0:
                    return index
                ternary_depth -= 1
            index += 1
        raise ValueError(f"{expr_path} has ternary operator '?' without matching ':'")

    def _split_top_level_commas(self, expression: str) -> list[str]:
        items: list[str] = []
        depth_paren = 0
        depth_bracket = 0
        depth_brace = 0
        cursor = 0
        index = 0
        length = len(expression)
        while index < length:
            char = expression[index]
            if char in {"'", '"'}:
                _, index = self._read_string_literal(expression, index)
                continue
            if char == "(":
                depth_paren += 1
            elif char == ")":
                depth_paren -= 1
            elif char == "[":
                depth_bracket += 1
            elif char == "]":
                depth_bracket -= 1
            elif char == "{":
                depth_brace += 1
            elif char == "}":
                depth_brace -= 1
            elif char == "," and depth_paren == 0 and depth_bracket == 0 and depth_brace == 0:
                items.append(expression[cursor:index])
                cursor = index + 1
            index += 1
        tail = expression[cursor:]
        if tail.strip():
            items.append(tail)
        return items

    def _find_top_level_colon(self, expression: str, expr_path: str) -> int:
        depth_paren = 0
        depth_bracket = 0
        depth_brace = 0
        index = 0
        length = len(expression)
        while index < length:
            char = expression[index]
            if char in {"'", '"'}:
                _, index = self._read_string_literal(expression, index)
                continue
            if char == "(":
                depth_paren += 1
            elif char == ")":
                depth_paren -= 1
            elif char == "[":
                depth_bracket += 1
            elif char == "]":
                depth_bracket -= 1
            elif char == "{":
                depth_brace += 1
            elif char == "}":
                depth_brace -= 1
            elif char == ":" and depth_paren == 0 and depth_bracket == 0 and depth_brace == 0:
                return index
            index += 1
        raise ValueError(f"{expr_path} object item is missing ':' separator")

    def _convert_cel_ternary_non_object(self, expression: str, expr_path: str) -> str:
        text = expression.strip()
        while self._is_wrapped_by_parentheses(text):
            text = text[1:-1].strip()

        question_index = self._find_top_level_ternary_question(text)
        if question_index < 0:
            return text

        colon_index = self._find_matching_ternary_colon(text, question_index, expr_path)
        condition_expr = self._convert_cel_ternary_to_python(text[:question_index], expr_path)
        true_expr = self._convert_cel_ternary_to_python(text[question_index + 1:colon_index], expr_path)
        false_expr = self._convert_cel_ternary_to_python(text[colon_index + 1:], expr_path)
        return f"({true_expr} if {condition_expr} else {false_expr})"

    def _convert_cel_object_literal(self, expression: str, expr_path: str) -> str:
        text = expression.strip()
        if text in {"{}", "{ }"}:
            return "{}"

        inner = text[1:-1]
        items = self._split_top_level_commas(inner)
        rendered_items: list[str] = []
        for index, item in enumerate(items):
            item_text = item.strip()
            if not item_text:
                continue
            item_expr_path = f"{expr_path}.object_item[{index}]"
            colon_index = self._find_top_level_colon(item_text, item_expr_path)
            key_part = item_text[:colon_index].strip()
            value_part = item_text[colon_index + 1:].strip()
            rendered_value = self._convert_cel_ternary_to_python(value_part, item_expr_path)
            rendered_items.append(f"{key_part}: {rendered_value}")
        if not rendered_items:
            return "{}"
        return "{ " + ", ".join(rendered_items) + " }"

    def _convert_cel_ternary_to_python(self, expression: str, expr_path: str) -> str:
        text = expression.strip()
        if text == "":
            return ""
        if text.startswith("{") and text.endswith("}"):
            return self._convert_cel_object_literal(text, expr_path)
        return self._convert_cel_ternary_non_object(text, expr_path)

    def _compile_has_argument_to_python(
        self,
        argument_expression: str,
        *,
        expr_path: str,
        input_source_node: str,
    ) -> tuple[str, bool]:
        text = argument_expression.strip()
        if text == "":
            raise ValueError(f"{expr_path} has() requires one non-empty argument")

        segments, index = self._read_reference_chain(text, 0)
        index = self._skip_spaces(text, index)
        if index != len(text):
            raise ValueError(f"{expr_path} has() supports only a single reference argument")
        if not segments:
            raise ValueError(f"{expr_path} has() requires one reference argument")

        root = segments[0]
        if not isinstance(root, str):
            raise ValueError(f"{expr_path} has() argument has invalid reference root")

        if root == "nodes":
            if len(segments) < 2 or not isinstance(segments[1], str):
                raise ValueError(f"{expr_path} has() supports nodes.<id> or nodes.<id>.output_parsed/output_text")
            node_id = segments[1]
            node_id_const = self._node_const_expression(node_id, expr_path=expr_path)
            responses_expr = "state[STATE_KEY_NODE_RESPONSES]"
            exists_expr = f"{node_id_const} in {responses_expr}"
            if len(segments) == 2:
                return exists_expr, True

            output_kind = segments[2]
            if not isinstance(output_kind, str) or output_kind not in {"output_parsed", "output_text"}:
                raise ValueError(f"{expr_path} has() supports nodes.<id> or nodes.<id>.output_parsed/output_text")

            node_expr = f"{responses_expr}[{node_id_const}]"
            conditions = [exists_expr]
            if output_kind == "output_text" and len(segments) > 3:
                raise ValueError(f"{expr_path} has(nodes.<id>.output_text) cannot be dereferenced further")

            current_expr = node_expr
            for segment in segments[3:]:
                if isinstance(segment, int):
                    conditions.append(f"len({current_expr}) > {segment}")
                    current_expr = f"{current_expr}[{segment}]"
                else:
                    conditions.append(f"{self._py_string(segment)} in {current_expr}")
                    current_expr = f"{current_expr}[{self._py_string(segment)}]"
            return f"({' and '.join(conditions)})", True

        if root == "state":
            if len(segments) < 2 or not isinstance(segments[1], str):
                raise ValueError(f"{expr_path} has() supports state.<field>")
            key = segments[1]
            key_expr = self._state_key_expr(key)
            conditions = [f"{key_expr} in state"]
            current_expr = f"state[{key_expr}]"
            for segment in segments[2:]:
                if isinstance(segment, int):
                    conditions.append(f"len({current_expr}) > {segment}")
                    current_expr = f"{current_expr}[{segment}]"
                else:
                    conditions.append(f"{self._py_string(segment)} in {current_expr}")
                    current_expr = f"{current_expr}[{self._py_string(segment)}]"
            return f"({' and '.join(conditions)})", False

        if root == "input":
            if len(segments) < 2:
                raise ValueError(f"{expr_path} has() supports input.output_parsed/output_text")
            output_kind = segments[1]
            if not isinstance(output_kind, str) or output_kind not in {"output_parsed", "output_text"}:
                raise ValueError(f"{expr_path} has() supports input.output_parsed/output_text")
            if input_source_node == "":
                raise ValueError(f"{expr_path} uses input.* in has() but node does not have exactly one upstream node")

            responses_expr = "state[STATE_KEY_NODE_RESPONSES]"
            input_source_const = self._node_const_expression(input_source_node, expr_path=expr_path)
            exists_expr = f"{input_source_const} in {responses_expr}"
            input_expr = f"{responses_expr}[{input_source_const}]"
            conditions = [exists_expr]
            if output_kind == "output_text" and len(segments) > 2:
                raise ValueError(f"{expr_path} has(input.output_text) cannot be dereferenced further")

            current_expr = input_expr
            for segment in segments[2:]:
                if isinstance(segment, int):
                    conditions.append(f"len({current_expr}) > {segment}")
                    current_expr = f"{current_expr}[{segment}]"
                else:
                    conditions.append(f"{self._py_string(segment)} in {current_expr}")
                    current_expr = f"{current_expr}[{self._py_string(segment)}]"
            return f"({' and '.join(conditions)})", True

        raise ValueError(f"{expr_path} has() supports only nodes.<id>(.output_parsed/output_text), "
                         "state.<field>, and input.output_parsed/output_text")

    def _compile_transform_expr_to_python(
        self,
        expression: str,
        *,
        expr_path: str,
        input_source_node: str,
    ) -> tuple[str, bool]:
        compiled_parts: list[str] = []
        needs_node_responses = False
        index = 0
        length = len(expression)
        while index < length:
            char = expression[index]
            if char in {"'", '"'}:
                literal, next_index = self._read_string_literal(expression, index)
                compiled_parts.append(literal)
                index = next_index
                continue

            if char == "&" and index + 1 < length and expression[index + 1] == "&":
                compiled_parts.append(" and ")
                index += 2
                continue
            if char == "|" and index + 1 < length and expression[index + 1] == "|":
                compiled_parts.append(" or ")
                index += 2
                continue

            if char.isalpha() or char == "_":
                segments, next_index = self._read_reference_chain(expression, index)
                index = next_index

                if len(segments) == 1:
                    token = segments[0]
                    if not isinstance(token, str):
                        raise ValueError(f"{expr_path} has invalid identifier token")
                    token_lower = token.lower()
                    if token_lower == "true":
                        compiled_parts.append("True")
                        continue
                    if token_lower == "false":
                        compiled_parts.append("False")
                        continue
                    if token_lower == "null":
                        compiled_parts.append("None")
                        continue

                    next_non_space = self._skip_spaces(expression, index)
                    if next_non_space < length and expression[next_non_space] == "(":
                        if token == "size":
                            compiled_parts.append("len")
                            continue
                        if token == "string":
                            compiled_parts.append("str")
                            continue
                        if token == "int":
                            compiled_parts.append("int")
                            continue
                        if token == "double":
                            compiled_parts.append("float")
                            continue
                        if token == "bool":
                            compiled_parts.append("bool")
                            continue
                        if token == "has":
                            inner_text, after_call_index = self._read_parenthesized_expression(
                                expression,
                                next_non_space,
                                expr_path=expr_path,
                                function_name="has",
                            )
                            compiled_has_expr, has_needs_node_responses = self._compile_has_argument_to_python(
                                inner_text,
                                expr_path=f"{expr_path}.has",
                                input_source_node=input_source_node,
                            )
                            if has_needs_node_responses:
                                needs_node_responses = True
                            compiled_parts.append(compiled_has_expr)
                            index = after_call_index
                            continue
                        raise ValueError(f"{expr_path} has unsupported function {token!r}; "
                                         "supported functions: size(), string(), int(), double(), bool(), has()")

                    raise ValueError(f"{expr_path} has unsupported identifier {token!r}; "
                                     "expression supports only state.*, input.*, and nodes.* references")

                next_non_space = self._skip_spaces(expression, index)
                if (isinstance(segments[-1], str) and segments[-1] == "contains" and next_non_space < length
                        and expression[next_non_space] == "("):
                    base_expr, base_needs_node_responses = self._compile_reference_chain_to_python(
                        segments[:-1],
                        expr_path=expr_path,
                        input_source_node=input_source_node,
                    )
                    if base_needs_node_responses:
                        needs_node_responses = True
                    compiled_parts.append(f"{base_expr}.__contains__")
                    continue

                ref_expr, ref_needs_node_responses = self._compile_reference_chain_to_python(
                    segments,
                    expr_path=expr_path,
                    input_source_node=input_source_node,
                )
                if ref_needs_node_responses:
                    needs_node_responses = True
                compiled_parts.append(ref_expr)
                continue

            compiled_parts.append(char)
            index += 1

        compiled_expression = "".join(compiled_parts)
        compiled_expression = self._convert_cel_ternary_to_python(compiled_expression, expr_path)
        try:
            ast.parse(compiled_expression, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"{expr_path} cannot be compiled as expression: {e.msg}") from e
        return compiled_expression, needs_node_responses

    def _build_node_ref_variable_name(self, node_id: str, output_kind: str) -> str:
        safe_node_id = self._node_symbol_lower(node_id)
        if output_kind == "output_parsed":
            return f"{safe_node_id}_output"
        return f"{safe_node_id}_output_text"

    def _build_node_name_variable_name(self, node_id: str) -> str:
        safe_node_id = self._node_symbol_lower(node_id)
        return f"{safe_node_id}_node_name"

    def _build_code_constant_name(self, node_id: str) -> str:
        return f"CODE_{self._node_symbol_upper(node_id)}"

    def _node_const_expression(self, node_id: str, *, expr_path: str) -> str:
        const_name = self.node_const_names.get(node_id)
        if const_name is None:
            raise ValueError(f"{expr_path} references unknown node id {node_id!r}")
        return const_name

    def _route_target_expression(self, target: str, *, expr_path: str) -> str:
        if target == "__end__":
            return "END"
        return self._node_const_expression(target, expr_path=expr_path)

    def _build_node_constants(self) -> list[dict[str, str]]:
        return [{
            "const_name": self.node_const_names[node.node_id],
            "value_literal": self._py_string(node.node_id),
        } for node in self.workflow.nodes]

    @staticmethod
    def _split_template_path(raw_path: str, path: str) -> tuple[str, ...]:
        segments = tuple(segment.strip() for segment in raw_path.split(".") if segment.strip())
        if not segments:
            raise ValueError(f"{path} must reference at least one path segment")
        return segments

    def _build_incoming_sources(self) -> dict[str, list[str]]:
        incoming: dict[str, list[str]] = {}

        def append_unique(target: str, source: str) -> None:
            if target == "__end__":
                return
            sources = incoming.setdefault(target, [])
            if source not in sources:
                sources.append(source)

        for edge in self.workflow.edges:
            append_unique(edge.target, edge.source)

        for cond in self.workflow.conditional_edges:
            for case in cond.condition.cases:
                append_unique(case.target, cond.from_node)
            if cond.condition.default:
                append_unique(cond.condition.default, cond.from_node)

        return incoming

    def _compile_template_reference(
        self,
        reference: str,
        *,
        expr_path: str,
        input_source_node: str,
    ) -> tuple[str, bool]:
        segments = self._parse_reference(reference, expr_path=expr_path)
        return self._compile_reference_chain_to_python(
            segments,
            expr_path=expr_path,
            input_source_node=input_source_node,
        )

    def _compile_template_query(
        self,
        template: str,
        *,
        expr_path: str,
        input_source_node: str,
    ) -> tuple[str, bool]:
        matches = list(_TEMPLATE_EXPR_PATTERN.finditer(template))
        if not matches:
            return self._py_string(template), False

        parts: list[str] = []
        needs_node_responses = False
        cursor = 0
        for match in matches:
            literal = template[cursor:match.start()]
            if literal:
                parts.append(self._py_string(literal))

            placeholder = match.group(1).strip()
            placeholder_expr, placeholder_needs_node_responses = self._compile_template_reference(
                placeholder,
                expr_path=expr_path,
                input_source_node=input_source_node,
            )
            if placeholder_needs_node_responses:
                needs_node_responses = True
            parts.append(f"str({placeholder_expr})")
            cursor = match.end()

        tail_literal = template[cursor:]
        if tail_literal:
            parts.append(self._py_string(tail_literal))

        if not parts:
            return "''", needs_node_responses
        if len(parts) == 1:
            return parts[0], needs_node_responses
        return " + ".join(parts), needs_node_responses

    def _compile_instruction_placeholder(
        self,
        reference: str,
        *,
        expr_path: str,
        input_source_node: str,
    ) -> tuple[str, str, bool, bool]:
        segments = self._parse_reference(reference, expr_path=expr_path)
        source_expr, uses_node_responses = self._compile_reference_chain_to_python(
            segments,
            expr_path=expr_path,
            input_source_node=input_source_node,
        )
        key_name = self._build_placeholder_key_name(segments)
        return key_name, source_expr, uses_node_responses, True

    def _build_agent_instruction_bindings(self) -> dict[str, dict[str, Any]]:
        if self._agent_instruction_bindings_cache is not None:
            return self._agent_instruction_bindings_cache

        incoming_sources = self._build_incoming_sources()
        bindings: dict[str, dict[str, Any]] = {}

        for node in self.agent_nodes:
            cfg = node.llm_config
            if cfg is None:
                continue

            instruction = cfg.instruction
            guidance_blocks: list[str] = []
            for tool in cfg.knowledge_search_tools:
                if not tool.agentic_filter_info:
                    continue
                tool_name = tool.name or "knowledge_search"
                block_lines: list[str] = [
                    f"Tool {tool_name!r} accepts an optional `dynamic_filter` argument.",
                    "Use JSON dynamic_filter format with operators: eq, ne, gt, gte, lt, lte, in, not in, like, not like, between, and, or.",
                    "Logical operators (and/or) must use `value` as an array of sub-conditions.",
                    "dynamic_filter JSON examples:",
                    "- Single: {\"field\":\"metadata.category\",\"operator\":\"eq\",\"value\":\"documentation\"}",
                    "- Logical: {\"operator\":\"and\",\"value\":[{\"field\":\"metadata.status\",\"operator\":\"eq\",\"value\":\"active\"}]}",
                    "Allowed dynamic_filter fields:",
                ]
                for field_name in sorted(tool.agentic_filter_info.keys()):
                    field_info = tool.agentic_filter_info[field_name]
                    if field_info.values:
                        values_repr = ", ".join(repr(value) for value in field_info.values)
                        line = f"- {field_name}: use exact values [{values_repr}]"
                    else:
                        line = f"- {field_name}: value can be inferred from user query"
                    if field_info.description:
                        line = f"{line}; {field_info.description}"
                    block_lines.append(line)
                if tool.knowledge_filter is not None:
                    static_filter_text = json.dumps(tool.knowledge_filter, ensure_ascii=True)
                    block_lines.append("A static knowledge_filter is already applied automatically.")
                    block_lines.append(f"Static filter expression: {static_filter_text}")
                    block_lines.append("Add only additional constraints in `dynamic_filter` when needed.")
                guidance_blocks.append("\n".join(block_lines))
            if guidance_blocks:
                instruction = f"{instruction}\n\nKnowledge Filter Guidance:\n\n" + "\n\n".join(guidance_blocks)

            source_nodes = incoming_sources.get(node.node_id, [])
            input_source_node = source_nodes[0] if len(source_nodes) == 1 else ""

            mapper_assignments: list[dict[str, str]] = []
            assignment_expr_by_key: dict[str, str] = {}
            needs_node_responses = False

            matches = list(_TEMPLATE_EXPR_PATTERN.finditer(instruction))
            if not matches:
                rendered_instruction = instruction
            else:
                rendered_parts: list[str] = []
                cursor = 0
                for index, match in enumerate(matches):
                    literal = instruction[cursor:match.start()]
                    if literal:
                        rendered_parts.append(literal)

                    placeholder = match.group(1).strip()
                    expr_path = f"nodes[{node.node_id}].config.instruction.placeholder[{index}]"
                    key_name, source_expr, uses_node_responses, needs_input_mapper = (
                        self._compile_instruction_placeholder(
                            placeholder,
                            expr_path=expr_path,
                            input_source_node=input_source_node,
                        ))
                    if uses_node_responses:
                        needs_node_responses = True

                    rendered_parts.append("{" + key_name + "}")

                    if needs_input_mapper:
                        existing_expr = assignment_expr_by_key.get(key_name)
                        if existing_expr is None:
                            assignment_expr_by_key[key_name] = source_expr
                            mapper_assignments.append({
                                "key_literal": self._py_string(key_name),
                                "source_expr": source_expr,
                            })
                        elif existing_expr != source_expr:
                            raise ValueError(
                                f"{expr_path} resolved placeholder key {key_name!r} with conflicting expressions")

                    cursor = match.end()

                tail_literal = instruction[cursor:]
                if tail_literal:
                    rendered_parts.append(tail_literal)

                rendered_instruction = "".join(rendered_parts)

            if cfg.user_message:
                um_expr_path = f"nodes[{node.node_id}].config.user_message"
                um_source_expr, um_needs_node_responses = self._compile_template_query(
                    cfg.user_message,
                    expr_path=um_expr_path,
                    input_source_node=input_source_node,
                )
                if um_needs_node_responses:
                    needs_node_responses = True
                mapper_assignments.append({
                    "key_literal": "STATE_KEY_USER_INPUT",
                    "source_expr": um_source_expr,
                })

            has_input_mapper = bool(mapper_assignments)
            bindings[node.node_id] = {
                "instruction": rendered_instruction,
                "input_mapper_func_name": self.agent_input_mapper_func_names[node.node_id] if has_input_mapper else "",
                "mapper_assignments": mapper_assignments,
                "needs_node_responses": needs_node_responses,
                "has_input_mapper": has_input_mapper,
            }

        self._agent_instruction_bindings_cache = bindings
        return bindings

    def _compile_set_state_expr_to_python(
        self,
        expression: str,
        *,
        expr_path: str,
        input_source_node: str,
    ) -> tuple[str, bool]:
        unsupported_tokens = ("?", " has(", " size(", " string(", ".contains(", "&&", "||")
        lowered = expression.lower()
        for token in unsupported_tokens:
            if token in lowered:
                raise ValueError(f"{expr_path} contains unsupported expression token: {token.strip()!r}")

        compiled_parts: list[str] = []
        needs_node_responses = False
        index = 0
        length = len(expression)

        while index < length:
            char = expression[index]
            if char in {"'", '"'}:
                literal, next_index = self._read_string_literal(expression, index)
                compiled_parts.append(literal)
                index = next_index
                continue

            if char.isalpha() or char == "_":
                segments, next_index = self._read_dotted_identifier(expression, index)
                index = next_index

                if len(segments) == 1:
                    token = segments[0]
                    if token == "true":
                        compiled_parts.append("True")
                        continue
                    if token == "false":
                        compiled_parts.append("False")
                        continue
                    if token == "null":
                        compiled_parts.append("None")
                        continue
                    raise ValueError(f"{expr_path} has unsupported identifier {token!r}; "
                                     "expression supports state.*, input.output_parsed/output_text, "
                                     "and nodes.<id>.output_parsed/output_text references")

                if segments[0] == "state":
                    compiled_parts.append(self._build_state_access_expression(segments))
                    continue

                if segments[0] == "input":
                    if len(segments) < 2 or segments[1] not in {"output_parsed", "output_text"}:
                        dotted = ".".join(segments)
                        raise ValueError(f"{expr_path} has unsupported reference {dotted!r}; "
                                         "input supports only input.output_parsed/output_text")
                    if input_source_node == "":
                        raise ValueError(f"{expr_path} uses input.output_parsed/input.output_text but "
                                         "node does not have exactly one upstream node")

                    input_source_const = self._node_const_expression(input_source_node, expr_path=expr_path)
                    ref_expr = f"state[STATE_KEY_NODE_RESPONSES][{input_source_const}]"
                    needs_node_responses = True
                    if segments[1] == "output_text" and len(segments) > 2:
                        dotted = ".".join(segments)
                        raise ValueError(f"{expr_path} has unsupported reference {dotted!r}; "
                                         "input.output_text cannot be dereferenced further")
                    start_index = 2
                    for tail_segment in segments[start_index:]:
                        ref_expr += f"[{self._py_string(tail_segment)}]"
                    compiled_parts.append(ref_expr)
                    continue

                if segments[0] == "nodes" and len(segments) >= 3 and segments[2] in {"output_parsed", "output_text"}:
                    node_id = segments[1]
                    output_kind = segments[2]
                    node_id_const = self._node_const_expression(node_id, expr_path=expr_path)
                    ref_expr = f"state[STATE_KEY_NODE_RESPONSES][{node_id_const}]"
                    needs_node_responses = True
                    if output_kind == "output_text" and len(segments) > 3:
                        dotted = ".".join(segments)
                        raise ValueError(f"{expr_path} has unsupported reference {dotted!r}; "
                                         "output_text cannot be dereferenced further")
                    for tail_segment in segments[3:]:
                        ref_expr += f"[{self._py_string(tail_segment)}]"
                    compiled_parts.append(ref_expr)
                    continue

                dotted = ".".join(segments)
                raise ValueError(f"{expr_path} has unsupported reference {dotted!r}; "
                                 "expression supports state.*, input.output_parsed/output_text, "
                                 "and nodes.<id>.output_parsed/output_text")

            compiled_parts.append(char)
            index += 1

        compiled_expression = "".join(compiled_parts)
        try:
            ast.parse(compiled_expression, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"{expr_path} cannot be compiled as expression: {e.msg}") from e
        return compiled_expression, needs_node_responses

    def _compile_end_expr_to_python(
        self,
        expression: str,
        expr_path: str,
    ) -> tuple[str, list[dict[str, str]]]:
        unsupported_tokens = ("?", " has(", " size(", " string(", ".contains(", "&&", "||")
        lowered = expression.lower()
        for token in unsupported_tokens:
            if token in lowered:
                raise ValueError(f"{expr_path} contains unsupported expression token: {token.strip()!r}")

        compiled_parts: list[str] = []
        node_ref_map: dict[tuple[str, str], dict[str, str]] = {}
        index = 0
        length = len(expression)

        while index < length:
            char = expression[index]
            if char in {"'", '"'}:
                literal, next_index = self._read_string_literal(expression, index)
                compiled_parts.append(literal)
                index = next_index
                continue

            if char.isalpha() or char == "_":
                segments, next_index = self._read_dotted_identifier(expression, index)
                index = next_index

                if len(segments) == 1:
                    token = segments[0]
                    if token == "true":
                        compiled_parts.append("True")
                        continue
                    if token == "false":
                        compiled_parts.append("False")
                        continue
                    if token == "null":
                        compiled_parts.append("None")
                        continue
                    raise ValueError(
                        f"{expr_path} has unsupported identifier {token!r}; "
                        "expression supports only state.* and nodes.*.output_parsed/output_text references")

                if segments[0] == "state":
                    compiled_parts.append(self._build_state_access_expression(segments))
                    continue

                if segments[0] == "nodes" and len(segments) >= 3 and segments[2] in {"output_parsed", "output_text"}:
                    node_id = segments[1]
                    output_kind = segments[2]
                    ref_key = (node_id, output_kind)
                    ref_meta = node_ref_map.get(ref_key)
                    if ref_meta is None:
                        node_id_const = self._node_const_expression(node_id, expr_path=expr_path)
                        ref_meta = {
                            "var_name": self._build_node_ref_variable_name(node_id, output_kind),
                            "node_var_name": self._build_node_name_variable_name(node_id),
                            "node_const_name": node_id_const,
                            "output_kind": output_kind,
                        }
                        node_ref_map[ref_key] = ref_meta

                    ref_expr = ref_meta["var_name"]
                    if output_kind == "output_text" and len(segments) > 3:
                        dotted = ".".join(segments)
                        raise ValueError(f"{expr_path} has unsupported reference {dotted!r}; "
                                         "output_text cannot be dereferenced further")
                    for tail_segment in segments[3:]:
                        ref_expr += f"[{tail_segment!r}]"
                    compiled_parts.append(ref_expr)
                    continue

                dotted = ".".join(segments)
                raise ValueError(f"{expr_path} has unsupported reference {dotted!r}; "
                                 "expression supports state.* and nodes.<id>.output_parsed/output_text")

            compiled_parts.append(char)
            index += 1

        compiled_expression = "".join(compiled_parts)
        try:
            ast.parse(compiled_expression, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"{expr_path} cannot be compiled as expression: {e.msg}") from e
        return compiled_expression, list(node_ref_map.values())

    def _build_state_context(self) -> dict[str, Any]:
        self._uses_literal = False
        self._uses_field = False
        self._uses_optional_field = False

        state_fields: list[dict[str, str]] = []
        for state_var in self.workflow.state_variables:
            kind = state_var.kind
            if kind == "string":
                annotation = "str"
            elif kind == "number":
                annotation = "float"
            elif kind == "boolean":
                annotation = "bool"
            elif kind == "object":
                annotation = "dict[str, Any]"
            elif kind == "array":
                annotation = "list[Any]"
            else:
                annotation = "Any"
            state_fields.append({
                "name": state_var.name,
                "annotation": annotation,
            })

        model_blocks: list[str] = []
        for node in self.agent_nodes:
            cfg = node.llm_config
            model_name = self.output_model_names.get(node.node_id)
            if cfg is None or model_name is None or cfg.output_schema is None:
                continue
            model_blocks.append(self._render_output_model_class(model_name, cfg.output_schema))

        typing_imports = ["Any"]
        if self._uses_literal:
            typing_imports.append("Literal")
        if self._uses_optional_field:
            typing_imports.append("Optional")

        pydantic_imports: list[str] = []
        if model_blocks:
            pydantic_imports = ["BaseModel", "ConfigDict"]
            if self._uses_field:
                pydantic_imports.append("Field")

        return {
            "typing_imports": typing_imports,
            "pydantic_imports": pydantic_imports,
            "model_blocks": model_blocks,
            "state_fields": state_fields,
            "has_state_fields": bool(state_fields),
        }

    def _build_prompts_context(self) -> dict[str, Any]:
        instruction_bindings = self._build_agent_instruction_bindings()
        prompts: list[dict[str, str]] = []
        for node in self.agent_nodes:
            cfg = node.llm_config
            if cfg is None:
                continue
            binding = instruction_bindings.get(node.node_id)
            if binding is None:
                continue
            instruction = binding["instruction"]
            prompts.append({
                "const_name": self.instruction_const_names[node.node_id],
                "instruction_literal": self._py_string(instruction),
            })
        return {"prompts": prompts}

    def _build_config_context(self) -> dict[str, Any]:
        model_functions: list[dict[str, Any]] = []
        mcp_toolset_functions: list[dict[str, Any]] = []
        knowledge_auth_functions: list[dict[str, Any]] = []
        seen_knowledge_auth_functions: set[str] = set()
        uses_os_env = False
        for node in self.agent_nodes:
            cfg = node.llm_config
            if cfg is None:
                continue
            model_env_names = self.model_env_names_by_node.get(node.node_id)
            if model_env_names is None:
                raise ValueError(f"Missing model env names for builtin.llmagent[{node.node_id}]")

            model_name_expr = "None"
            if cfg.model_spec.model_name is not None:
                uses_os_env = True
                model_name_expr = f"os.getenv({self._py_string(model_env_names['model_name'])})"
            api_key_expr = "None"
            if cfg.model_spec.api_key is not None:
                uses_os_env = True
                api_key_expr = f"os.getenv({self._py_string(model_env_names['api_key'])})"
            base_url_expr = "None"
            if cfg.model_spec.base_url is not None:
                uses_os_env = True
                base_url_expr = f"os.getenv({self._py_string(model_env_names['base_url'])})"

            model_functions.append({
                "func_name": self.model_func_names[node.node_id],
                "model_name_expr": model_name_expr,
                "api_key_expr": api_key_expr,
                "base_url_expr": base_url_expr,
                "headers_literal": repr(cfg.model_spec.headers),
            })
            for index, tool in enumerate(cfg.knowledge_search_tools, start=1):
                connector = tool.connector
                if connector.connector_type not in _SUPPORTED_KNOWLEDGE_CONNECTOR_TYPES:
                    continue
                auth_func_name = self._build_knowledge_auth_func_name(
                    node.node_id,
                    index,
                    connector.connector_type,
                )
                if auth_func_name not in seen_knowledge_auth_functions:
                    knowledge_env_names = self.knowledge_env_names_by_auth_func.get(auth_func_name)
                    if knowledge_env_names is None:
                        raise ValueError(f"Missing knowledge env names for function {auth_func_name!r}")
                    uses_os_env = True
                    seen_knowledge_auth_functions.add(auth_func_name)
                    type_expr = f"os.getenv({self._py_string(knowledge_env_names['type'])})"
                    endpoint_expr = "None"
                    if connector.endpoint is not None:
                        endpoint_expr = f"os.getenv({self._py_string(knowledge_env_names['endpoint'])})"
                    token_expr = "None"
                    if connector.token is not None:
                        token_expr = f"os.getenv({self._py_string(knowledge_env_names['token'])})"
                    rag_code_expr = "None"
                    if connector.rag_code is not None:
                        rag_code_expr = f"os.getenv({self._py_string(knowledge_env_names['rag_code'])})"
                    namespace_expr = "None"
                    if connector.namespace is not None:
                        namespace_expr = f"os.getenv({self._py_string(knowledge_env_names['namespace'])})"
                    collection_expr = "None"
                    if connector.collection is not None:
                        collection_expr = f"os.getenv({self._py_string(knowledge_env_names['collection'])})"
                    knowledge_base_id_expr = "\"\""
                    if connector.knowledge_base_id is not None:
                        knowledge_base_id_expr = (
                            f"os.getenv({self._py_string(knowledge_env_names['knowledge_base_id'])})"
                            " or \"\"")
                    knowledge_auth_functions.append({
                        "func_name": auth_func_name,
                        "connector_type": connector.connector_type,
                        "type_expr": type_expr,
                        "endpoint_expr": endpoint_expr,
                        "token_expr": token_expr,
                        "rag_code_expr": rag_code_expr,
                        "namespace_expr": namespace_expr,
                        "collection_expr": collection_expr,
                        "knowledge_base_id_expr": knowledge_base_id_expr,
                        "headers_literal": repr(connector.headers),
                    })

        for node in self.supported_knowledge_nodes:
            cfg = node.knowledge_config
            if cfg is None:
                raise ValueError(f"builtin.knowledge_search[{node.node_id}] has no parsed config")
            connector = cfg.connector

            auth_func_name = self.knowledge_node_auth_func_names[node.node_id]
            if auth_func_name not in seen_knowledge_auth_functions:
                knowledge_env_names = self.knowledge_env_names_by_auth_func.get(auth_func_name)
                if knowledge_env_names is None:
                    raise ValueError(f"Missing knowledge env names for function {auth_func_name!r}")
                uses_os_env = True
                seen_knowledge_auth_functions.add(auth_func_name)
                type_expr = f"os.getenv({self._py_string(knowledge_env_names['type'])})"
                endpoint_expr = "None"
                if connector.endpoint is not None:
                    endpoint_expr = f"os.getenv({self._py_string(knowledge_env_names['endpoint'])})"
                token_expr = "None"
                if connector.token is not None:
                    token_expr = f"os.getenv({self._py_string(knowledge_env_names['token'])})"
                rag_code_expr = "None"
                if connector.rag_code is not None:
                    rag_code_expr = f"os.getenv({self._py_string(knowledge_env_names['rag_code'])})"
                namespace_expr = "None"
                if connector.namespace is not None:
                    namespace_expr = f"os.getenv({self._py_string(knowledge_env_names['namespace'])})"
                collection_expr = "None"
                if connector.collection is not None:
                    collection_expr = f"os.getenv({self._py_string(knowledge_env_names['collection'])})"
                knowledge_base_id_expr = "\"\""
                if connector.knowledge_base_id is not None:
                    knowledge_base_id_expr = (f"os.getenv({self._py_string(knowledge_env_names['knowledge_base_id'])})"
                                              " or \"\"")
                knowledge_auth_functions.append({
                    "func_name": auth_func_name,
                    "connector_type": connector.connector_type,
                    "type_expr": type_expr,
                    "endpoint_expr": endpoint_expr,
                    "token_expr": token_expr,
                    "rag_code_expr": rag_code_expr,
                    "namespace_expr": namespace_expr,
                    "collection_expr": collection_expr,
                    "knowledge_base_id_expr": knowledge_base_id_expr,
                    "headers_literal": repr(connector.headers),
                })

        for node in self.mcp_nodes:
            cfg = node.mcp_config
            if cfg is None:
                raise ValueError(f"builtin.mcp[{node.node_id}] has no parsed config")
            mcp_tool = cfg.mcp
            connection_class = "SseConnectionParams" if mcp_tool.transport == "sse" else "StreamableHTTPConnectionParams"
            server_url_env_name = self.mcp_env_name_by_node.get(node.node_id)
            if server_url_env_name is None:
                raise ValueError(f"Missing mcp env names for builtin.mcp[{node.node_id}]")
            uses_os_env = True
            mcp_toolset_functions.append({
                "func_name": self.mcp_toolset_func_names[node.node_id],
                "node_id_literal": self._py_string(node.node_id),
                "connection_class": connection_class,
                "server_url_env_name_literal": self._py_string(server_url_env_name),
                "headers_literal": repr(mcp_tool.headers),
                "has_headers": bool(mcp_tool.headers),
                "timeout_literal": repr(mcp_tool.timeout),
                "has_timeout": mcp_tool.timeout is not None,
                "allowed_tools_literal": repr(list(mcp_tool.allowed_tools)),
                "has_allowed_tools": bool(mcp_tool.allowed_tools),
            })

        skill_repository_functions: list[dict[str, Any]] = []
        for node in self.agent_nodes:
            cfg = node.llm_config
            if cfg is None or cfg.skills is None:
                continue
            executor_type = "local"
            work_dir = ""
            language_enum_expr = ""
            secret_id_env_name_literal = ""
            secret_key_env_name_literal = ""
            execute_timeout_literal = "0.0"
            idle_timeout_literal = "0.0"
            shared_literal = "False"
            interactive_literal = "False"
            has_execute_timeout = False
            has_idle_timeout = False
            has_shared = False
            has_interactive = False
            if cfg.executor is not None:
                executor_type = cfg.executor.type
                if executor_type == "local":
                    work_dir = cfg.executor.work_dir or ""
                elif executor_type == "pcg123":
                    language_enum_expr = self._pcg_language_enum_expr(cfg.executor.language)
                    pcg_env_names = self.skill_pcg_env_names_by_node.get(node.node_id)
                    if pcg_env_names is None:
                        raise ValueError(f"Missing pcg123 env names for builtin.llmagent[{node.node_id}]")
                    uses_os_env = True
                    secret_id_env_name_literal = self._py_string(pcg_env_names["secret_id"])
                    secret_key_env_name_literal = self._py_string(pcg_env_names["secret_key"])
                    if cfg.executor.execute_timeout_seconds is not None:
                        has_execute_timeout = True
                        execute_timeout_literal = repr(cfg.executor.execute_timeout_seconds)
                    if cfg.executor.idle_timeout_seconds is not None:
                        has_idle_timeout = True
                        idle_timeout_literal = repr(cfg.executor.idle_timeout_seconds)
                    if cfg.executor.shared is not None:
                        has_shared = True
                        shared_literal = "True" if cfg.executor.shared else "False"
                    if cfg.executor.interactive is not None:
                        has_interactive = True
                        interactive_literal = "True" if cfg.executor.interactive else "False"
                else:
                    raise ValueError(f"Unsupported skills executor type: {executor_type!r}")
            root_env_names = self.skill_root_env_names_by_node.get(node.node_id)
            if root_env_names is None:
                raise ValueError(f"Missing skill root env names for builtin.llmagent[{node.node_id}]")
            uses_os_env = True
            skill_repository_functions.append({
                "func_name": self.skill_repository_and_toolset_func_names[node.node_id],
                "root_env_names_literal": repr(tuple(root_env_names)),
                "work_dir_literal": self._py_string(work_dir),
                "only_active_skills": cfg.skills.load_mode == "turn",
                "executor_type": executor_type,
                "language_enum_expr": language_enum_expr,
                "secret_id_env_name_literal": secret_id_env_name_literal,
                "secret_key_env_name_literal": secret_key_env_name_literal,
                "has_execute_timeout": has_execute_timeout,
                "execute_timeout_literal": execute_timeout_literal,
                "has_idle_timeout": has_idle_timeout,
                "idle_timeout_literal": idle_timeout_literal,
                "has_shared": has_shared,
                "shared_literal": shared_literal,
                "has_interactive": has_interactive,
                "interactive_literal": interactive_literal,
            })

        code_executor_functions: list[dict[str, Any]] = []
        for node in self.code_nodes:
            code_cfg = node.code_config
            if code_cfg is None:
                raise ValueError(f"builtin.code[{node.node_id}] has no parsed config")
            executor = code_cfg.executor
            if executor.type == "local":
                code_executor_functions.append({
                    "func_name": self.code_executor_func_names[node.node_id],
                    "executor_type": "local",
                    "timeout_literal": repr(code_cfg.timeout_seconds),
                    "work_dir_literal": self._py_string(code_cfg.work_dir),
                    "clean_temp_files_literal": "True" if code_cfg.clean_temp_files else "False",
                    "language_enum_expr": "",
                    "secret_id_env_name_literal": "",
                    "secret_key_env_name_literal": "",
                    "has_execute_timeout": False,
                    "execute_timeout_literal": "0.0",
                    "has_idle_timeout": False,
                    "idle_timeout_literal": "0.0",
                    "has_shared": False,
                    "shared_literal": "False",
                    "has_interactive": False,
                    "interactive_literal": "False",
                })
                continue

            if executor.type != "pcg123":
                raise ValueError(f"builtin.code[{node.node_id}] executor type {executor.type!r} is not supported yet")
            pcg_env_names = self.code_pcg_env_names_by_node.get(node.node_id)
            if pcg_env_names is None:
                raise ValueError(f"Missing pcg123 env names for builtin.code[{node.node_id}]")
            uses_os_env = True
            has_execute_timeout = executor.execute_timeout_seconds is not None
            has_idle_timeout = executor.idle_timeout_seconds is not None
            has_shared = executor.shared is not None
            has_interactive = executor.interactive is not None
            code_executor_functions.append({
                "func_name":
                self.code_executor_func_names[node.node_id],
                "executor_type":
                "pcg123",
                "timeout_literal":
                repr(code_cfg.timeout_seconds),
                "work_dir_literal":
                self._py_string(code_cfg.work_dir),
                "clean_temp_files_literal":
                "True" if code_cfg.clean_temp_files else "False",
                "language_enum_expr":
                self._pcg_language_enum_expr(executor.language),
                "secret_id_env_name_literal":
                self._py_string(pcg_env_names["secret_id"]),
                "secret_key_env_name_literal":
                self._py_string(pcg_env_names["secret_key"]),
                "has_execute_timeout":
                has_execute_timeout,
                "execute_timeout_literal":
                repr(executor.execute_timeout_seconds) if has_execute_timeout else "0.0",
                "has_idle_timeout":
                has_idle_timeout,
                "idle_timeout_literal":
                repr(executor.idle_timeout_seconds) if has_idle_timeout else "0.0",
                "has_shared":
                has_shared,
                "shared_literal":
                "True" if executor.shared else "False",
                "has_interactive":
                has_interactive,
                "interactive_literal":
                "True" if executor.interactive else "False",
            })

        has_local_skill_repository_functions = any(item["executor_type"] == "local"
                                                   for item in skill_repository_functions)
        has_pcg_skill_repository_functions = any(item["executor_type"] == "pcg123"
                                                 for item in skill_repository_functions)
        has_local_code_executor_functions = any(item["executor_type"] == "local" for item in code_executor_functions)
        has_pcg_code_executor_functions = any(item["executor_type"] == "pcg123" for item in code_executor_functions)

        return {
            "uses_os_env":
            uses_os_env,
            "model_functions":
            model_functions,
            "mcp_toolset_functions":
            mcp_toolset_functions,
            "has_mcp_toolset_functions":
            bool(mcp_toolset_functions),
            "knowledge_auth_functions":
            knowledge_auth_functions,
            "has_knowledge_auth_functions":
            bool(knowledge_auth_functions),
            "has_trag_knowledge_auth_functions":
            any(auth_func["connector_type"] == "trag" for auth_func in knowledge_auth_functions),
            "has_lingshan_knowledge_auth_functions":
            any(auth_func["connector_type"] == "lingshan" for auth_func in knowledge_auth_functions),
            "skill_repository_functions":
            skill_repository_functions,
            "has_skill_repository_functions":
            bool(skill_repository_functions),
            "has_local_skill_repository_functions":
            has_local_skill_repository_functions,
            "has_pcg_skill_repository_functions":
            has_pcg_skill_repository_functions,
            "code_executor_functions":
            code_executor_functions,
            "has_code_executor_functions":
            bool(code_executor_functions),
            "has_local_code_executor_functions":
            has_local_code_executor_functions,
            "has_pcg_code_executor_functions":
            has_pcg_code_executor_functions,
        }

    def _build_tools_context(self) -> dict[str, Any]:
        tool_functions: list[dict[str, Any]] = []
        knowledge_node_tool_functions: list[dict[str, Any]] = []
        knowledge_auth_imports: list[str] = []
        seen_knowledge_auth_imports: set[str] = set()
        for node in self.agent_nodes:
            cfg = node.llm_config
            if cfg is None or (not cfg.mcp_tools and not cfg.knowledge_search_tools and not cfg.memory_search_tools):
                continue

            connections: list[dict[str, Any]] = []
            for index, tool in enumerate(cfg.mcp_tools, start=1):
                connection_class = "SseConnectionParams" if tool.transport == "sse" else "StreamableHTTPConnectionParams"
                url_env_name = self.agent_mcp_env_name_by_tool.get((node.node_id, index))
                if url_env_name is None:
                    raise ValueError(f"Missing MCP env names for builtin.llmagent[{node.node_id}].tools[{index}]")
                connections.append({
                    "var_name": f"connection_params_{index}",
                    "connection_class": connection_class,
                    "url_env_name_literal": self._py_string(url_env_name),
                    "headers_literal": repr(tool.headers),
                    "has_headers": bool(tool.headers),
                    "timeout_literal": repr(tool.timeout),
                    "has_timeout": tool.timeout is not None,
                    "allowed_tools_literal": repr(list(tool.allowed_tools)),
                    "has_allowed_tools": bool(tool.allowed_tools),
                })

            knowledge_tools: list[dict[str, Any]] = []
            for index, tool in enumerate(cfg.knowledge_search_tools, start=1):
                connector = tool.connector
                if connector.connector_type not in _SUPPORTED_KNOWLEDGE_CONNECTOR_TYPES:
                    continue

                knowledge_tools.append({
                    "index":
                    index,
                    "connector_type":
                    connector.connector_type,
                    "tool_name_literal":
                    self._py_string(tool.name or "knowledge_search"),
                    "tool_description_literal":
                    self._py_string(tool.description or "Search for relevant information in the knowledge base", ),
                    "auth_params_func_name":
                    self._build_knowledge_auth_func_name(
                        node.node_id,
                        index,
                        connector.connector_type,
                    ),
                    "knowledge_factory_func_name":
                    "_create_trag_knowledge" if connector.connector_type == "trag" else "_create_lingshan_knowledge",
                    "max_results_literal":
                    repr(tool.max_results),
                    "min_score_literal":
                    repr(tool.min_score),
                    "knowledge_filter_expr_literal":
                    "None" if tool.knowledge_filter is None else
                    f"KnowledgeFilterExpr.model_validate({repr(tool.knowledge_filter)})",
                    "has_agentic_filter":
                    bool(tool.agentic_filter_info),
                })
                auth_import = self._build_knowledge_auth_func_name(
                    node.node_id,
                    index,
                    connector.connector_type,
                )
                if auth_import not in seen_knowledge_auth_imports:
                    seen_knowledge_auth_imports.add(auth_import)
                    knowledge_auth_imports.append(auth_import)

            tool_functions.append({
                "func_name": self.tool_func_names[node.node_id],
                "connections": connections,
                "has_mcp_connections": bool(connections),
                "has_memory_search_tools": bool(cfg.memory_search_tools),
                "knowledge_tools": knowledge_tools,
                "has_knowledge_tools": bool(knowledge_tools),
            })

        for node in self.supported_knowledge_nodes:
            cfg = node.knowledge_config
            if cfg is None:
                raise ValueError(f"builtin.knowledge_search[{node.node_id}] has no parsed config")

            connector = cfg.connector

            auth_import = self.knowledge_node_auth_func_names[node.node_id]
            if auth_import not in seen_knowledge_auth_imports:
                seen_knowledge_auth_imports.add(auth_import)
                knowledge_auth_imports.append(auth_import)

            knowledge_node_tool_functions.append({
                "func_name":
                self.knowledge_node_tool_func_names[node.node_id],
                "connector_type":
                connector.connector_type,
                "auth_params_func_name":
                auth_import,
                "knowledge_factory_func_name":
                "_create_trag_knowledge" if connector.connector_type == "trag" else "_create_lingshan_knowledge",
                "max_results_literal":
                repr(cfg.max_results),
                "min_score_literal":
                repr(cfg.min_score),
                "knowledge_filter_expr_literal":
                "None" if cfg.knowledge_filter is None else
                f"KnowledgeFilterExpr.model_validate({repr(cfg.knowledge_filter)})",
            })

        code_constants: list[dict[str, str]] = []
        for node in self.code_nodes:
            cfg = node.code_config
            if cfg is None:
                continue
            code_constants.append({
                "const_name": self._build_code_constant_name(node.node_id),
                "code_literal": self._py_string(cfg.code),
            })

        return {
            "tool_functions":
            tool_functions,
            "has_tool_functions":
            bool(tool_functions),
            "has_any_mcp_tools":
            any(tool_func["has_mcp_connections"] for tool_func in tool_functions),
            "has_any_memory_search_tools":
            any(tool_func["has_memory_search_tools"] for tool_func in tool_functions),
            "has_any_knowledge_tools":
            any(tool_func["has_knowledge_tools"] for tool_func in tool_functions),
            "knowledge_node_tool_functions":
            knowledge_node_tool_functions,
            "has_knowledge_node_tool_functions":
            bool(knowledge_node_tool_functions),
            "has_trag_knowledge_helpers":
            any(knowledge_tool["connector_type"] == "trag" for tool_func in tool_functions
                for knowledge_tool in tool_func["knowledge_tools"])
            or any(knowledge_node_tool["connector_type"] == "trag"
                   for knowledge_node_tool in knowledge_node_tool_functions),
            "has_lingshan_knowledge_helpers":
            any(knowledge_tool["connector_type"] == "lingshan" for tool_func in tool_functions
                for knowledge_tool in tool_func["knowledge_tools"])
            or any(knowledge_node_tool["connector_type"] == "lingshan"
                   for knowledge_node_tool in knowledge_node_tool_functions),
            "has_any_knowledge_helpers":
            any(tool_func["has_knowledge_tools"]
                for tool_func in tool_functions) or bool(knowledge_node_tool_functions),
            "knowledge_auth_imports":
            knowledge_auth_imports,
            "code_constants":
            code_constants,
            "has_code_constants":
            bool(code_constants),
        }

    def _build_nodes_context(self) -> dict[str, Any]:
        route_functions: list[dict[str, Any]] = []
        needs_end_in_route_targets = False
        route_needs_node_responses = False
        incoming_sources = self._build_incoming_sources()
        instruction_bindings = self._build_agent_instruction_bindings()
        for index, edge in enumerate(self.workflow.conditional_edges):
            route_key = edge.edge_id or f"{edge.from_node}:{index}"
            source_const = self.node_const_names[edge.from_node]
            cases: list[dict[str, str]] = []
            for case_index, case in enumerate(edge.condition.cases):
                predicate_expr_path = (
                    f"conditional_edges[{edge.from_node}].condition.cases[{case_index}].predicate.expression")
                expr_format = case.predicate.format.strip().lower()
                if expr_format not in {"", "cel"}:
                    raise ValueError(f"{predicate_expr_path} format {case.predicate.format!r} is not supported")
                predicate_expr, needs_node_responses = self._compile_transform_expr_to_python(
                    case.predicate.expression,
                    expr_path=predicate_expr_path,
                    input_source_node=edge.from_node,
                )
                if needs_node_responses:
                    route_needs_node_responses = True
                target_expr = self._route_target_expression(
                    case.target,
                    expr_path=f"conditional_edges[{edge.from_node}].condition.cases[{case_index}].target",
                )
                if target_expr == "END":
                    needs_end_in_route_targets = True
                cases.append({
                    "predicate_expr": predicate_expr,
                    "target_expr": target_expr,
                })

            default_expr = ""
            if edge.condition.default:
                default_expr = self._route_target_expression(
                    edge.condition.default,
                    expr_path=f"conditional_edges[{edge.from_node}].condition.default",
                )
                if default_expr == "END":
                    needs_end_in_route_targets = True
            route_functions.append({
                "func_name": self.route_func_names[route_key],
                "cases": cases,
                "default_expr": default_expr,
                "error_expr": f"\"No conditional case matched for route from \" + {source_const}",
            })

        user_approval_node_functions: list[dict[str, str]] = []
        user_approval_route_functions: list[dict[str, str]] = []
        for node in self.user_approval_nodes:
            cfg = node.user_approval_config
            if cfg is None:
                raise ValueError(f"builtin.user_approval[{node.node_id}] has no parsed config")
            user_approval_node_functions.append({
                "func_name": self.node_func_names[node.node_id],
                "node_const_name": self.node_const_names[node.node_id],
                "message_literal": self._py_string(cfg.message),
            })
            route_key = f"user_approval:{node.node_id}"
            approve_target_expr = self._route_target_expression(
                cfg.routing.approve,
                expr_path=f"nodes[{node.node_id}].config.routing.approve",
            )
            reject_target_expr = self._route_target_expression(
                cfg.routing.reject,
                expr_path=f"nodes[{node.node_id}].config.routing.reject",
            )
            if approve_target_expr == "END" or reject_target_expr == "END":
                needs_end_in_route_targets = True
            user_approval_route_functions.append({
                "func_name":
                self.route_func_names[route_key],
                "node_const_name":
                self.node_const_names[node.node_id],
                "approve_target_expr":
                approve_target_expr,
                "reject_target_expr":
                reject_target_expr,
                "error_expr":
                f"f\"Approval decision for {{{self.node_const_names[node.node_id]}}} "
                "must be 'approve' or 'reject'\"",
            })

        start_node_functions = [{
            "func_name": self.node_func_names[node.node_id],
        } for node in self.start_nodes]
        agent_input_mapper_functions: list[dict[str, Any]] = []
        agent_mapper_needs_node_responses = False
        for node in self.agent_nodes:
            binding = instruction_bindings.get(node.node_id)
            if binding is None:
                continue
            if binding["needs_node_responses"]:
                agent_mapper_needs_node_responses = True
            if not binding["has_input_mapper"]:
                continue
            agent_input_mapper_functions.append({
                "func_name": binding["input_mapper_func_name"],
                "assignments": binding["mapper_assignments"],
                "has_assignments": bool(binding["mapper_assignments"]),
            })
        transform_node_functions: list[dict[str, Any]] = []
        transform_needs_node_responses = False
        for node in self.transform_nodes:
            cfg = node.transform_config
            if cfg is None:
                raise ValueError(f"builtin.transform[{node.node_id}] has no parsed config")

            source_nodes = incoming_sources.get(node.node_id, [])
            input_source_node = source_nodes[0] if len(source_nodes) == 1 else ""

            compiled_expr = ""
            has_expr = False
            if cfg.expr and cfg.expr.expression.strip():
                expr_path = f"nodes[{node.node_id}].config.expr.expression"
                expr_format = cfg.expr.format.strip().lower()
                if expr_format in {"", "cel"}:
                    compiled_expr, needs_node_responses = self._compile_transform_expr_to_python(
                        cfg.expr.expression,
                        expr_path=expr_path,
                        input_source_node=input_source_node,
                    )
                    if needs_node_responses:
                        transform_needs_node_responses = True
                elif expr_format == "json":
                    try:
                        literal_obj = json.loads(cfg.expr.expression)
                    except json.JSONDecodeError as e:
                        raise ValueError(f"{expr_path} with format='json' must be valid JSON: {e.msg}") from e
                    compiled_expr = repr(literal_obj)
                else:
                    raise ValueError(f"{expr_path} format {cfg.expr.format!r} is not supported")
                has_expr = True

            transform_node_functions.append({
                "func_name": self.node_func_names[node.node_id],
                "node_const_name": self.node_const_names[node.node_id],
                "has_expr": has_expr,
                "compiled_expr_code": compiled_expr,
            })
        end_node_functions: list[dict[str, Any]] = []
        has_end_expr_nodes = False
        end_needs_node_responses = False
        for node in self.end_nodes:
            cfg = node.end_config
            compiled_expr = ""
            has_expr = False
            if cfg and cfg.expr and cfg.expr.expression.strip():
                expr_path = f"nodes[{node.node_id}].config.expr.expression"
                expr_format = cfg.expr.format.strip().lower()
                source_nodes = incoming_sources.get(node.node_id, [])
                input_source_node = source_nodes[0] if len(source_nodes) == 1 else ""
                if expr_format in {"", "cel"}:
                    compiled_expr, needs_node_responses = self._compile_transform_expr_to_python(
                        cfg.expr.expression,
                        expr_path=expr_path,
                        input_source_node=input_source_node,
                    )
                    if needs_node_responses:
                        end_needs_node_responses = True
                elif expr_format == "json":
                    try:
                        literal_obj = json.loads(cfg.expr.expression)
                    except json.JSONDecodeError as e:
                        raise ValueError(f"{expr_path} with format='json' must be valid JSON: {e.msg}") from e
                    compiled_expr = repr(literal_obj)
                else:
                    raise ValueError(f"{expr_path} format {cfg.expr.format!r} is not supported")
                has_expr = True
                has_end_expr_nodes = True

            end_node_functions.append({
                "func_name": self.node_func_names[node.node_id],
                "node_const_name": self.node_const_names[node.node_id],
                "has_expr": has_expr,
                "compiled_expr_code": compiled_expr,
            })
        custom_like_nodes = self.custom_nodes + self.unsupported_knowledge_nodes
        custom_node_functions = [{
            "func_name": self.node_func_names[node.node_id],
            "node_type_literal": self._py_string(node.node_type),
            "node_id_literal": self._py_string(node.node_id),
        } for node in custom_like_nodes]
        set_state_node_functions: list[dict[str, Any]] = []
        set_state_needs_node_responses = False
        for node in self.set_state_nodes:
            cfg = node.set_state_config
            if cfg is None:
                raise ValueError(f"builtin.set_state[{node.node_id}] has no parsed config")

            source_nodes = incoming_sources.get(node.node_id, [])
            input_source_node = source_nodes[0] if len(source_nodes) == 1 else ""

            assignments: list[dict[str, str]] = []
            for index, assignment in enumerate(cfg.assignments):
                expr_path = f"nodes[{node.node_id}].config.assignments[{index}].expr.expression"
                expr_format = assignment.expr.format.strip().lower()
                if expr_format not in {"", "cel"}:
                    raise ValueError(f"{expr_path} format {assignment.expr.format!r} is not supported")
                expr_code, needs_node_responses = self._compile_transform_expr_to_python(
                    assignment.expr.expression,
                    expr_path=expr_path,
                    input_source_node=input_source_node,
                )
                if needs_node_responses:
                    set_state_needs_node_responses = True
                assignments.append({
                    "field_literal": self._py_string(assignment.field),
                    "expr_code": expr_code,
                })

            set_state_node_functions.append({
                "func_name": self.node_func_names[node.node_id],
                "assignments": assignments,
                "has_assignments": bool(assignments),
            })
        knowledge_query_functions: list[dict[str, str]] = []
        knowledge_needs_node_responses = False
        for node in self.supported_knowledge_nodes:
            cfg = node.knowledge_config
            if cfg is None:
                raise ValueError(f"builtin.knowledge_search[{node.node_id}] has no parsed config")

            query_template = cfg.query
            query_path = f"nodes[{node.node_id}].config.query"
            placeholder_matches = list(_TEMPLATE_EXPR_PATTERN.finditer(query_template))
            uses_input_reference = any(match.group(1).strip().startswith("input.") for match in placeholder_matches)

            input_source_node = ""
            if uses_input_reference:
                source_nodes = incoming_sources.get(node.node_id, [])
                if len(source_nodes) != 1:
                    raise ValueError(
                        f"{query_path} uses input.* and requires exactly one upstream node; got {len(source_nodes)}")
                input_source_node = source_nodes[0]

            query_expr_code, needs_node_responses = self._compile_template_query(
                query_template,
                expr_path=query_path,
                input_source_node=input_source_node,
            )
            if needs_node_responses:
                knowledge_needs_node_responses = True

            knowledge_query_functions.append({
                "func_name": self.knowledge_query_func_names[node.node_id],
                "query_expr_code": query_expr_code,
            })

        state_key_imports: set[str] = set()

        def collect_state_keys_from_expr(expr_code: str) -> None:
            for const_name in re.findall(r"\bSTATE_KEY_[A-Z0-9_]+\b", expr_code):
                state_key_imports.add(const_name)

        for route in route_functions:
            for case in route["cases"]:
                collect_state_keys_from_expr(case["predicate_expr"])

        for mapper in agent_input_mapper_functions:
            for assignment in mapper["assignments"]:
                collect_state_keys_from_expr(assignment["source_expr"])

        for node in transform_node_functions:
            if node["has_expr"]:
                collect_state_keys_from_expr(node["compiled_expr_code"])

        for node in end_node_functions:
            if node["has_expr"]:
                collect_state_keys_from_expr(node["compiled_expr_code"])
                state_key_imports.add("STATE_KEY_LAST_RESPONSE")

        for node in set_state_node_functions:
            for assignment in node["assignments"]:
                collect_state_keys_from_expr(assignment["expr_code"])

        for node in knowledge_query_functions:
            collect_state_keys_from_expr(node["query_expr_code"])

        if any(node["has_expr"] for node in transform_node_functions):
            state_key_imports.add("STATE_KEY_NODE_RESPONSES")
        if any(node["has_expr"] for node in end_node_functions):
            state_key_imports.add("STATE_KEY_NODE_RESPONSES")
        if user_approval_node_functions or user_approval_route_functions:
            state_key_imports.add("STATE_KEY_NODE_RESPONSES")

        return {
            "node_constants":
            self._build_node_constants(),
            "route_functions":
            route_functions,
            "needs_input_output_parsed":
            False,
            "needs_node_responses":
            route_needs_node_responses or bool(user_approval_node_functions) or end_needs_node_responses
            or has_end_expr_nodes or knowledge_needs_node_responses or set_state_needs_node_responses
            or bool(transform_node_functions) or transform_needs_node_responses or agent_mapper_needs_node_responses,
            "has_user_approval_nodes":
            bool(user_approval_node_functions),
            "has_end_expr_nodes":
            has_end_expr_nodes,
            "start_node_functions":
            start_node_functions,
            "agent_input_mapper_functions":
            agent_input_mapper_functions,
            "has_agent_input_mapper_functions":
            bool(agent_input_mapper_functions),
            "transform_node_functions":
            transform_node_functions,
            "has_transform_nodes":
            bool(transform_node_functions),
            "end_node_functions":
            end_node_functions,
            "user_approval_node_functions":
            user_approval_node_functions,
            "user_approval_route_functions":
            user_approval_route_functions,
            "set_state_node_functions":
            set_state_node_functions,
            "has_set_state_nodes":
            bool(set_state_node_functions),
            "custom_node_functions":
            custom_node_functions,
            "knowledge_query_functions":
            knowledge_query_functions,
            "has_knowledge_query_functions":
            bool(knowledge_query_functions),
            "state_key_imports":
            sorted(state_key_imports),
            "needs_end_in_route_targets":
            needs_end_in_route_targets,
        }

    def _build_agent_context(self) -> dict[str, Any]:
        instruction_bindings = self._build_agent_instruction_bindings()
        incoming_sources = self._build_incoming_sources()
        model_imports = [self.model_func_names[node.node_id] for node in self.agent_nodes]
        mcp_config_imports = [self.mcp_toolset_func_names[node.node_id] for node in self.mcp_nodes]
        skill_repo_imports = [
            self.skill_repository_and_toolset_func_names[node.node_id] for node in self.agent_nodes
            if node.node_id in self.skill_repository_and_toolset_func_names
        ]
        code_executor_imports = [
            self.code_executor_func_names[node.node_id] for node in self.code_nodes
            if node.node_id in self.code_executor_func_names
        ]
        config_imports = list(
            dict.fromkeys(model_imports + mcp_config_imports + skill_repo_imports + code_executor_imports))
        agent_input_mapper_imports = [
            instruction_bindings[node.node_id]["input_mapper_func_name"] for node in self.agent_nodes
            if node.node_id in instruction_bindings and instruction_bindings[node.node_id]["has_input_mapper"]
        ]
        node_imports = ([self.node_func_names[node.node_id] for node in self.start_nodes] +
                        [self.node_func_names[node.node_id] for node in self.transform_nodes] +
                        [self.node_func_names[node.node_id] for node in self.end_nodes] +
                        [self.node_func_names[node.node_id] for node in self.set_state_nodes] +
                        [self.node_func_names[node.node_id] for node in self.user_approval_nodes] +
                        [self.node_func_names[node.node_id] for node in self.custom_nodes] +
                        [self.node_func_names[node.node_id] for node in self.unsupported_knowledge_nodes] +
                        [self.knowledge_query_func_names[node.node_id]
                         for node in self.supported_knowledge_nodes] + agent_input_mapper_imports)
        node_constant_imports = [self.node_const_names[node.node_id] for node in self.workflow.nodes]
        route_imports = []
        for index, edge in enumerate(self.workflow.conditional_edges):
            route_key = edge.edge_id or f"{edge.from_node}:{index}"
            route_imports.append(self.route_func_names[route_key])
        for node in self.user_approval_nodes:
            route_key = f"user_approval:{node.node_id}"
            route_imports.append(self.route_func_names[route_key])
        prompt_imports = [self.instruction_const_names[node.node_id] for node in self.agent_nodes]
        state_imports = ["WorkflowState"] + [
            self.output_model_names[node.node_id]
            for node in self.agent_nodes if node.node_id in self.output_model_names
        ]
        tool_imports = [
            self.tool_func_names[node.node_id] for node in self.agent_nodes if node.node_id in self.tool_func_names
        ]
        knowledge_node_tool_imports = [
            self.knowledge_node_tool_func_names[node.node_id] for node in self.supported_knowledge_nodes
        ]
        code_imports: list[str] = []

        builders: list[dict[str, Any]] = []
        for node in self.agent_nodes:
            cfg = node.llm_config
            if cfg is None:
                continue

            generation_args: list[dict[str, str]] = []
            if cfg.temperature is not None:
                generation_args.append({"name": "temperature", "value_code": repr(cfg.temperature)})
            if cfg.max_tokens is not None:
                generation_args.append({"name": "max_output_tokens", "value_code": repr(cfg.max_tokens)})
            if cfg.top_p is not None:
                generation_args.append({"name": "top_p", "value_code": repr(cfg.top_p)})

            tool_func_name = self.tool_func_names.get(node.node_id, "")
            output_model_name = self.output_model_names.get(node.node_id, "")
            has_skills = cfg.skills is not None
            skill_repository_and_toolset_func_name = (self.skill_repository_and_toolset_func_names.get(
                node.node_id, ""))
            builders.append({
                "builder_name": self.agent_builder_names[node.node_id],
                "node_name_expr": self.node_const_names[node.node_id],
                "description_literal": self._py_string(node.label or node.node_id),
                "model_func_name": self.model_func_names[node.node_id],
                "instruction_const": self.instruction_const_names[node.node_id],
                "tools_expr": f"{tool_func_name}()" if tool_func_name else "[]",
                "generation_args": generation_args,
                "output_model_name": output_model_name,
                "has_skills": has_skills,
                "skill_repository_and_toolset_func_name": skill_repository_and_toolset_func_name,
            })

        start_node_defs = [{
            "const_name": self.node_const_names[node.node_id],
            "func_name": self.node_func_names[node.node_id],
            "name_literal": self._py_string(node.node_id),
            "description_literal": self._py_string(node.label or node.node_id),
        } for node in self.start_nodes]
        transform_node_defs = [{
            "const_name": self.node_const_names[node.node_id],
            "func_name": self.node_func_names[node.node_id],
            "name_literal": self._py_string(node.node_id),
            "description_literal": self._py_string(node.label or node.node_id),
        } for node in self.transform_nodes]
        mcp_node_defs = [{
            "const_name":
            self.node_const_names[node.node_id],
            "toolset_func_name":
            self.mcp_toolset_func_names[node.node_id],
            "selected_tool_name_literal":
            self._py_string(node.mcp_config.function if node.mcp_config else ""),
            "req_src_node_literal":
            self._py_string(""),
            "name_literal":
            self._py_string(node.node_id),
            "description_literal":
            self._py_string(node.label or node.node_id),
        } for node in self.mcp_nodes]
        for index, node in enumerate(self.mcp_nodes):
            source_nodes = incoming_sources.get(node.node_id, [])
            if len(source_nodes) != 1:
                raise ValueError(
                    f"builtin.mcp[{node.node_id}] requires exactly one upstream node; got {len(source_nodes)}")
            mcp_node_defs[index]["req_src_node_literal"] = self._py_string(source_nodes[0])
        end_node_defs = [{
            "const_name": self.node_const_names[node.node_id],
            "func_name": self.node_func_names[node.node_id],
            "name_literal": self._py_string(node.node_id),
            "description_literal": self._py_string(node.label or node.node_id),
        } for node in self.end_nodes]
        user_approval_node_defs = [{
            "const_name": self.node_const_names[node.node_id],
            "func_name": self.node_func_names[node.node_id],
            "name_literal": self._py_string(node.node_id),
            "description_literal": self._py_string(node.label or node.node_id),
        } for node in self.user_approval_nodes]
        set_state_node_defs = [{
            "const_name": self.node_const_names[node.node_id],
            "func_name": self.node_func_names[node.node_id],
            "name_literal": self._py_string(node.node_id),
            "description_literal": self._py_string(node.label or node.node_id),
        } for node in self.set_state_nodes]
        code_node_defs: list[dict[str, Any]] = []
        for node in self.code_nodes:
            code_cfg = node.code_config
            if code_cfg is None:
                continue
            code_const_name = self._build_code_constant_name(node.node_id)
            code_imports.append(code_const_name)
            code_node_defs.append({
                "const_name": self.node_const_names[node.node_id],
                "name_literal": self._py_string(node.node_id),
                "description_literal": self._py_string(node.label or node.node_id),
                "code_const_name": code_const_name,
                "language_literal": self._py_string(code_cfg.language),
                "code_executor_func_name": self.code_executor_func_names[node.node_id],
            })
        custom_like_nodes = self.custom_nodes + self.unsupported_knowledge_nodes
        custom_node_defs = [{
            "const_name": self.node_const_names[node.node_id],
            "func_name": self.node_func_names[node.node_id],
            "name_literal": self._py_string(node.node_id),
            "description_literal": self._py_string(node.label or node.node_id),
        } for node in custom_like_nodes]
        knowledge_node_defs: list[dict[str, Any]] = []
        for node in self.supported_knowledge_nodes:
            cfg = node.knowledge_config
            if cfg is None:
                raise ValueError(f"builtin.knowledge_search[{node.node_id}] has no parsed config")
            knowledge_node_defs.append({
                "const_name": self.node_const_names[node.node_id],
                "query_func_name": self.knowledge_query_func_names[node.node_id],
                "tool_func_name": self.knowledge_node_tool_func_names[node.node_id],
                "name_literal": self._py_string(node.node_id),
                "description_literal": self._py_string(node.label or node.node_id),
            })

        agent_node_defs: list[dict[str, Any]] = []
        for node in self.agent_nodes:
            cfg = node.llm_config
            if cfg is None:
                continue
            binding = instruction_bindings.get(node.node_id)
            agent_node_defs.append({
                "const_name":
                self.node_const_names[node.node_id],
                "builder_name":
                self.agent_builder_names[node.node_id],
                "name_literal":
                self._py_string(node.node_id),
                "description_literal":
                self._py_string(node.label or node.node_id),
                "node_id_literal":
                self._py_string(node.node_id),
                "input_mapper_func_name":
                "" if binding is None else binding["input_mapper_func_name"],
                "has_input_mapper":
                False if binding is None else binding["has_input_mapper"],
            })

        edges: list[dict[str, str]] = []
        for edge in self.workflow.edges:
            source_expr = "END" if edge.source == "__end__" else self.node_const_names[edge.source]
            target_expr = "END" if edge.target == "__end__" else self.node_const_names[edge.target]
            edges.append({
                "source_expr": source_expr,
                "target_expr": target_expr,
            })

        conditional_edges: list[dict[str, str]] = []
        for index, edge in enumerate(self.workflow.conditional_edges):
            route_key = edge.edge_id or f"{edge.from_node}:{index}"
            conditional_edges.append({
                "source_const": self.node_const_names[edge.from_node],
                "route_func": self.route_func_names[route_key],
            })
        for node in self.user_approval_nodes:
            route_key = f"user_approval:{node.node_id}"
            conditional_edges.append({
                "source_const": self.node_const_names[node.node_id],
                "route_func": self.route_func_names[route_key],
            })

        return {
            "config_imports": config_imports,
            "node_imports": node_imports,
            "node_constant_imports": node_constant_imports,
            "route_imports": route_imports,
            "prompt_imports": prompt_imports,
            "state_imports": state_imports,
            "tool_imports": tool_imports,
            "knowledge_node_tool_imports": knowledge_node_tool_imports,
            "code_imports": code_imports,
            "builders": builders,
            "start_node_defs": start_node_defs,
            "transform_node_defs": transform_node_defs,
            "mcp_node_defs": mcp_node_defs,
            "end_node_defs": end_node_defs,
            "set_state_node_defs": set_state_node_defs,
            "user_approval_node_defs": user_approval_node_defs,
            "code_node_defs": code_node_defs,
            "has_code_nodes": bool(code_node_defs),
            "knowledge_node_defs": knowledge_node_defs,
            "custom_node_defs": custom_node_defs,
            "agent_node_defs": agent_node_defs,
            "edges": edges,
            "conditional_edges": conditional_edges,
            "entry_point_const": self.node_const_names[self.workflow.start_node_id],
            "workflow_name_literal": self._py_string(self.workflow.name),
            "workflow_description_literal": self._py_string(self.workflow.description or self.workflow.name),
        }

    def _get_service_protocol(self) -> str:
        if self.service_mode is None:
            return ""
        if self.service_mode == "http":
            return "http"
        if self.service_mode == "a2a":
            return "a2a"
        return "ag_ui"

    def _build_service_context(self) -> dict[str, Any]:
        has_memory_search_tools = any(node.llm_config is not None and bool(node.llm_config.memory_search_tools)
                                      for node in self.agent_nodes)
        return {
            "service_mode": self.service_mode or "",
            "is_http_mode": self.service_mode == "http",
            "is_a2a_mode": self.service_mode == "a2a",
            "is_agui_mode": self.service_mode == "agui",
            "service_protocol": self._get_service_protocol(),
            "service_name": "trpc.py_trpc_agent.helloworld.Greeter",
            "service_host": "127.0.0.1",
            "service_port": 8080,
            "server_app": "py_trpc_agent",
            "server_name": "helloworld",
            "agui_uri": "/weather_agent",
            "has_memory_search_tools": has_memory_search_tools,
        }

    def _build_requirements_context(self) -> dict[str, Any]:
        return {
            "has_service": self.service_mode is not None,
            "service_mode": self.service_mode or "",
        }

    def _build_run_agent_context(self) -> dict[str, Any]:
        has_memory_search_tools = any(node.llm_config is not None and bool(node.llm_config.memory_search_tools)
                                      for node in self.agent_nodes)
        return {
            "app_name_literal": self._py_string(self.workflow.name),
            "has_interrupt_nodes": bool(self.user_approval_nodes),
            "has_memory_search_tools": has_memory_search_tools,
        }

    def _build_readme_context(self) -> dict[str, Any]:
        env_hints = [entry["name"] for entry in self.dotenv_entries]

        service_notes: list[str] = []
        client_steps: list[str] = []
        has_service = self.service_mode is not None
        if self.service_mode == "http":
            service_notes.append("Protocol: HTTP + SSE")
            client_steps.append("python client.py")
        elif self.service_mode == "a2a":
            service_notes.append("Protocol: A2A")
            service_notes.append("Client config file: trpc_python_client.yaml")
            client_steps.append("python client.py")
        elif self.service_mode == "agui":
            service_notes.append("Protocol: AG-UI")
            service_notes.append("AG-UI route: /weather_agent")
            client_steps.append("python client.py")
        else:
            service_notes.append("No service integration generated. Local runner only.")

        return {
            "workflow_name": self.workflow.name,
            "service_mode": self.service_mode,
            "has_service": has_service,
            "env_hints": env_hints,
            "service_notes": service_notes,
            "client_steps": client_steps,
        }

    def _build_dotenv_context(self) -> dict[str, Any]:
        env_entries = [{
            "name": entry["name"],
            "value_literal": self._dotenv_quote(entry["value"]),
        } for entry in self.dotenv_entries]
        return {"env_entries": env_entries}

    def render(self) -> dict[str, str]:
        rendered_files: dict[str, str] = {
            "agent/__init__.py":
            self._render_template("agent/__init__.py.tpl"),
            "agent/callbacks.py":
            self._render_template("agent/callbacks.py.tpl"),
            "agent/agent.py":
            self._render_template("agent/agent.py.tpl", **self._build_agent_context()),
            "agent/config.py":
            self._render_template("agent/config.py.tpl", **self._build_config_context()),
            "agent/prompts.py":
            self._render_template("agent/prompts.py.tpl", **self._build_prompts_context()),
            "agent/tools.py":
            self._render_template("agent/tools.py.tpl", **self._build_tools_context()),
            "agent/state.py":
            self._render_template("agent/state.py.tpl", **self._build_state_context()),
            "agent/nodes.py":
            self._render_template("agent/nodes.py.tpl", **self._build_nodes_context()),
            "run_agent.py":
            self._render_template("project/common/run_agent.py.tpl", **self._build_run_agent_context()),
            "README.md":
            self._render_template("project/common/README.md.tpl", **self._build_readme_context()),
            ".env":
            self._render_template("project/common/.env.tpl", **self._build_dotenv_context()),
            "requirements.txt":
            self._render_template("project/common/requirements.txt.tpl", **self._build_requirements_context()),
        }

        if self.service_mode is not None:
            service_context = self._build_service_context()
            rendered_files["trpc_main.py"] = self._render_template("project/common/trpc_main.py.tpl", **service_context)
            rendered_files["trpc_python.yaml"] = self._render_template("project/common/trpc_python.yaml.tpl",
                                                                       **service_context)

            if self.service_mode == "http":
                rendered_files["_agent_runner.py"] = self._render_template("project/service/http/_agent_runner.py.tpl",
                                                                           **service_context)
                rendered_files["http_service.py"] = self._render_template("project/service/http/http_service.py.tpl")
                rendered_files["client.py"] = self._render_template("project/service/http/client.py.tpl")
            elif self.service_mode == "a2a":
                rendered_files["a2a_service.py"] = self._render_template("project/service/a2a/a2a_service.py.tpl",
                                                                         **service_context)
                rendered_files["client.py"] = self._render_template("project/service/a2a/client.py.tpl")
                rendered_files["trpc_python_client.yaml"] = self._render_template(
                    "project/service/a2a/trpc_python_client.yaml.tpl", **service_context)
            else:
                rendered_files["agui_service.py"] = self._render_template("project/service/agui/agui_service.py.tpl",
                                                                          **service_context)
                rendered_files["client.py"] = self._render_template("project/service/agui/client.py.tpl",
                                                                    **service_context)

        return rendered_files


def render_workflow_files(workflow: WorkflowDefinition, service_mode: Optional[str] = None) -> dict[str, str]:
    """Render a workflow into a runnable Python project."""
    return _Renderer(workflow, service_mode=service_mode).render()
