# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Workflow definition loader for DSL codegen."""

import ast
import json
import re
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from pathlib import Path
from typing import Any
from typing import Optional

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

SUPPORTED_NODE_TYPES = frozenset({
    "builtin.start",
    "builtin.llmagent",
    "builtin.end",
    "builtin.mcp",
    "builtin.transform",
    "builtin.code",
    "builtin.knowledge_search",
    "builtin.set_state",
    "builtin.user_approval",
})

_EQ_STRING_PATTERN = re.compile(r"""^\s*(?P<left>.+?)\s*==\s*(?P<right>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')\s*$""")
_SUPPORTED_STATE_KINDS = frozenset({"string", "number", "boolean", "object", "array", "opaque"})
_BUILTIN_STATE_KEYS = frozenset({
    STATE_KEY_MESSAGES,
    STATE_KEY_USER_INPUT,
    STATE_KEY_LAST_RESPONSE,
    STATE_KEY_LAST_RESPONSE_ID,
    STATE_KEY_LAST_TOOL_RESPONSE,
    STATE_KEY_NODE_RESPONSES,
    STATE_KEY_ONE_SHOT_MESSAGES,
    STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE,
    STATE_KEY_METADATA,
    STATE_KEY_SESSION,
    STATE_KEY_CURRENT_NODE_ID,
    STATE_KEY_EXEC_CONTEXT,
    STATE_KEY_TOOL_CALLBACKS,
    STATE_KEY_MODEL_CALLBACKS,
    STATE_KEY_AGENT_CALLBACKS,
    STATE_KEY_NODE_CALLBACKS,
    STATE_KEY_STEP_NUMBER,
})

_REF_INPUT_OUTPUT_PARSED = "input.output_parsed"
_REF_STATE = "state"


@dataclass(frozen=True)
class ModelSpec:
    """Model specification for builtin.llmagent."""

    provider: str
    model_name: Optional[str]
    api_key: Optional[str]
    base_url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPToolSpec:
    """MCP tool configuration attached to a llmagent node."""

    name: str
    server_url: str
    transport: str = "streamable_http"
    timeout: Optional[float] = None
    headers: dict[str, str] = field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemorySearchToolSpec:
    """Memory search tool configuration attached to a llmagent node."""

    type: str = "memory_search"


@dataclass(frozen=True)
class AgenticFilterFieldInfo:
    """Guidance for a single agentic filter field."""

    values: Optional[tuple[Any, ...]] = None
    description: str = ""


@dataclass(frozen=True)
class KnowledgeConnectorSpec:
    """Knowledge connector configuration for knowledge search tools."""

    connector_type: str
    endpoint: Optional[str]
    token: Optional[str] = None
    rag_code: Optional[str] = None
    namespace: Optional[str] = None
    collection: Optional[str] = None
    knowledge_base_id: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeSearchToolSpec:
    """Knowledge search tool configuration attached to a llmagent node."""

    name: str
    description: str
    connector: KnowledgeConnectorSpec
    max_results: int = 10
    min_score: float = 0.0
    knowledge_filter: Optional[dict[str, Any]] = None
    agentic_filter_info: dict[str, AgenticFilterFieldInfo] = field(default_factory=dict)


@dataclass(frozen=True)
class Expression:
    """Conditional expression definition."""

    expression: str
    format: str = "cel"


@dataclass(frozen=True)
class ExpressionReference:
    """Resolved CEL reference path."""

    root: str
    segments: tuple[str, ...]


@dataclass(frozen=True)
class CompiledPredicate:
    """Compiled predicate from supported CEL subset."""

    reference: ExpressionReference
    expected_string: str


@dataclass(frozen=True)
class ConditionalCase:
    """A route case in conditional edges."""

    target: str
    predicate: Expression
    name: str = ""
    compiled_predicate: Optional[CompiledPredicate] = None


@dataclass(frozen=True)
class ConditionalRule:
    """Condition block in conditional edge."""

    cases: tuple[ConditionalCase, ...]
    default: str = ""


@dataclass(frozen=True)
class SkillsSpec:
    """Agent Skills configuration for builtin.llmagent."""

    roots: tuple[str, ...]
    load_mode: str = "turn"


@dataclass(frozen=True)
class ExecutorSpec:
    """Executor/workspace configuration for builtin.llmagent (e.g. skill run)."""

    type: str = "local"
    work_dir: str = ""
    workspace_mode: str = "isolated"
    timeout_seconds: Optional[int] = None
    clean_temp_files: Optional[bool] = None
    language: str = ""
    secret_id: str = ""
    secret_key: str = ""
    execute_timeout_seconds: Optional[float] = None
    idle_timeout_seconds: Optional[float] = None
    shared: Optional[bool] = None
    interactive: Optional[bool] = None


@dataclass(frozen=True)
class LLMNodeConfig:
    """Configuration of builtin.llmagent node."""

    model_spec: ModelSpec
    instruction: str
    description: str
    user_message: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    output_schema: Optional[dict[str, Any]] = None
    output_mode: str = "text"
    mcp_tools: tuple[MCPToolSpec, ...] = ()
    memory_search_tools: tuple[MemorySearchToolSpec, ...] = ()
    knowledge_search_tools: tuple[KnowledgeSearchToolSpec, ...] = ()
    skills: Optional[SkillsSpec] = None
    executor: Optional[ExecutorSpec] = None
    stream: Optional[bool] = None


@dataclass(frozen=True)
class EndNodeConfig:
    """Configuration of builtin.end node."""

    output_schema: Optional[dict[str, Any]] = None
    expr: Optional[Expression] = None


@dataclass(frozen=True)
class TransformNodeConfig:
    """Configuration of builtin.transform node."""

    output_schema: Optional[dict[str, Any]] = None
    expr: Optional[Expression] = None


@dataclass(frozen=True)
class CodeNodeConfig:
    """Configuration of builtin.code node."""

    code: str
    language: str
    executor: ExecutorSpec
    timeout_seconds: int = 30
    work_dir: str = ""
    clean_temp_files: bool = True


@dataclass(frozen=True)
class KnowledgeNodeConfig:
    """Configuration of builtin.knowledge_search node."""

    query: str
    connector: KnowledgeConnectorSpec
    max_results: int = 10
    min_score: float = 0.0
    knowledge_filter: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class MCPNodeConfig:
    """Configuration of builtin.mcp node."""

    mcp: MCPToolSpec
    function: str
    output_schema: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class UserApprovalRouting:
    """Routing targets for builtin.user_approval."""

    approve: str
    reject: str


@dataclass(frozen=True)
class UserApprovalConfig:
    """Configuration of builtin.user_approval node."""

    message: str
    routing: UserApprovalRouting


@dataclass(frozen=True)
class SetStateAssignment:
    """Single assignment in builtin.set_state."""

    field: str
    expr: Expression


@dataclass(frozen=True)
class SetStateConfig:
    """Configuration of builtin.set_state node."""

    assignments: tuple[SetStateAssignment, ...] = ()


@dataclass(frozen=True)
class NodeOutputBinding:
    """Node output mapping to workflow state."""

    name: str
    output_type: str
    target_type: str
    target_field: str


@dataclass(frozen=True)
class NodeDefinition:
    """Node definition."""

    node_id: str
    node_type: str
    label: str = ""
    llm_config: Optional[LLMNodeConfig] = None
    end_config: Optional[EndNodeConfig] = None
    transform_config: Optional[TransformNodeConfig] = None
    code_config: Optional[CodeNodeConfig] = None
    mcp_config: Optional[MCPNodeConfig] = None
    knowledge_config: Optional[KnowledgeNodeConfig] = None
    set_state_config: Optional[SetStateConfig] = None
    user_approval_config: Optional[UserApprovalConfig] = None
    outputs: tuple[NodeOutputBinding, ...] = ()


@dataclass(frozen=True)
class EdgeDefinition:
    """Directed edge definition."""

    source: str
    target: str
    edge_id: str = ""


@dataclass(frozen=True)
class ConditionalEdgeDefinition:
    """Conditional edge definition."""

    from_node: str
    condition: ConditionalRule
    edge_id: str = ""


@dataclass(frozen=True)
class StateVariableDefinition:
    """Workflow-level state variable declaration."""

    name: str
    kind: str = "opaque"
    json_schema: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class WorkflowDefinition:
    """Top-level workflow definition used by renderer."""

    name: str
    description: str
    start_node_id: str
    version: str
    nodes: tuple[NodeDefinition, ...]
    edges: tuple[EdgeDefinition, ...]
    conditional_edges: tuple[ConditionalEdgeDefinition, ...]
    state_variables: tuple[StateVariableDefinition, ...] = ()


def _expect_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _expect_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    return value


def _expect_string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    text = value.strip()
    if text == "":
        raise ValueError(f"{path} must be a non-empty string")
    return text


def _read_optional_string(value: Any, path: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    return value.strip()


def _read_optional_config_string(value: Any, path: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    text = value.strip()
    if text == "":
        return None
    return text


def _read_optional_number(value: Any, path: str) -> Optional[float]:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a number")
    return float(value)


def _read_optional_int(value: Any, path: str) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    return value


def _read_optional_bool(value: Any, path: str) -> Optional[bool]:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    return value


def _parse_model_spec(raw: Any, path: str) -> ModelSpec:
    data = _expect_dict(raw, path)
    provider = _expect_string(data.get("provider"), f"{path}.provider")
    model_name = _read_optional_config_string(data.get("model_name"), f"{path}.model_name")
    api_key = _read_optional_config_string(data.get("api_key"), f"{path}.api_key")
    base_url = _read_optional_config_string(data.get("base_url"), f"{path}.base_url")

    headers: dict[str, str] = {}
    headers_raw = data.get("headers")
    if headers_raw is not None:
        headers_data = _expect_dict(headers_raw, f"{path}.headers")
        for key, value in headers_data.items():
            if not isinstance(key, str) or key.strip() == "":
                raise ValueError(f"{path}.headers contains an invalid header key")
            if not isinstance(value, str):
                raise ValueError(f"{path}.headers[{key!r}] must be a string")
            headers[key] = value

    return ModelSpec(
        provider=provider,
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        headers=headers,
    )


def _parse_mcp_tool(raw: Any, path: str) -> MCPToolSpec:
    data = _expect_dict(raw, path)
    server_url = _expect_string(data.get("server_url"), f"{path}.server_url")
    name = _read_optional_string(data.get("name"), f"{path}.name")
    transport = _read_optional_string(data.get("transport"), f"{path}.transport") or "streamable_http"
    if transport not in {"sse", "streamable_http"}:
        raise ValueError(f"{path}.transport must be 'sse' or 'streamable_http'")

    timeout = _read_optional_number(data.get("timeout"), f"{path}.timeout")

    headers: dict[str, str] = {}
    headers_raw = data.get("headers")
    if headers_raw is not None:
        headers_data = _expect_dict(headers_raw, f"{path}.headers")
        for key, value in headers_data.items():
            if not isinstance(key, str) or key.strip() == "":
                raise ValueError(f"{path}.headers contains an invalid header key")
            if not isinstance(value, str):
                raise ValueError(f"{path}.headers[{key!r}] must be a string")
            headers[key] = value

    allowed_tools_raw = data.get("allowed_tools", [])
    allowed_tools: list[str] = []
    if allowed_tools_raw is not None:
        for item in _expect_list(allowed_tools_raw, f"{path}.allowed_tools"):
            allowed_tools.append(_expect_string(item, f"{path}.allowed_tools[]"))

    return MCPToolSpec(
        name=name,
        server_url=server_url,
        transport=transport,
        timeout=timeout,
        headers=headers,
        allowed_tools=tuple(allowed_tools),
    )


def _parse_memory_search_tool(raw: Any, path: str) -> MemorySearchToolSpec:
    data = _expect_dict(raw, path)
    tool_type = _expect_string(data.get("type"), f"{path}.type")
    if tool_type != "memory_search":
        raise ValueError(f"{path}.type must be 'memory_search'")
    return MemorySearchToolSpec(type=tool_type)


def _parse_json_value(raw: Any, path: str) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (str, int, float, bool)):
        return raw
    if isinstance(raw, list):
        return [_parse_json_value(item, f"{path}[]") for item in raw]
    if isinstance(raw, dict):
        result: dict[str, Any] = {}
        for key, value in raw.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string key")
            result[key] = _parse_json_value(value, f"{path}.{key}")
        return result
    raise ValueError(f"{path} must be a JSON-compatible value")


def _parse_agentic_filter_info(raw: Any, path: str) -> dict[str, AgenticFilterFieldInfo]:
    if raw is None:
        return {}
    data = _expect_dict(raw, path)
    parsed: dict[str, AgenticFilterFieldInfo] = {}
    for field, info_raw in data.items():
        if not isinstance(field, str) or field.strip() == "":
            raise ValueError(f"{path} contains an invalid field name")
        field_name = field.strip()

        values: Optional[tuple[Any, ...]] = None
        description = ""
        if info_raw is None:
            parsed[field_name] = AgenticFilterFieldInfo()
            continue
        if isinstance(info_raw, list):
            items = [_parse_json_value(item, f"{path}.{field_name}.values[]") for item in info_raw]
            values = tuple(items)
            parsed[field_name] = AgenticFilterFieldInfo(values=values)
            continue
        info_obj = _expect_dict(info_raw, f"{path}.{field_name}")
        values_raw = info_obj.get("values")
        if values_raw is not None:
            values_items = _expect_list(values_raw, f"{path}.{field_name}.values")
            values = tuple(_parse_json_value(item, f"{path}.{field_name}.values[]") for item in values_items)
        description = _read_optional_string(info_obj.get("description"), f"{path}.{field_name}.description")
        parsed[field_name] = AgenticFilterFieldInfo(values=values, description=description)
    return parsed


def _parse_knowledge_connector(raw: Any, path: str) -> KnowledgeConnectorSpec:
    connector_raw = _expect_dict(raw, path)
    connector_type = _expect_string(connector_raw.get("type"), f"{path}.type").lower()
    endpoint = _read_optional_config_string(connector_raw.get("endpoint"), f"{path}.endpoint")

    if connector_type == "trag":
        token = _read_optional_config_string(connector_raw.get("token"), f"{path}.token")
        rag_code = _read_optional_config_string(connector_raw.get("rag_code"), f"{path}.rag_code")
        namespace = _read_optional_config_string(connector_raw.get("namespace"), f"{path}.namespace")
        collection = _read_optional_config_string(connector_raw.get("collection"), f"{path}.collection")
        return KnowledgeConnectorSpec(
            connector_type=connector_type,
            endpoint=endpoint,
            token=token,
            rag_code=rag_code,
            namespace=namespace,
            collection=collection,
        )

    if connector_type == "lingshan":
        knowledge_base_id = _read_optional_config_string(connector_raw.get("knowledge_base_id"),
                                                         f"{path}.knowledge_base_id")
        headers: dict[str, str] = {}
        headers_raw = connector_raw.get("headers")
        if headers_raw is not None:
            headers_data = _expect_dict(headers_raw, f"{path}.headers")
            for key, value in headers_data.items():
                if not isinstance(key, str) or key.strip() == "":
                    raise ValueError(f"{path}.headers contains an invalid header key")
                if not isinstance(value, str):
                    raise ValueError(f"{path}.headers[{key!r}] must be a string")
                headers[key] = value
        return KnowledgeConnectorSpec(
            connector_type=connector_type,
            endpoint=endpoint,
            knowledge_base_id=knowledge_base_id,
            headers=headers,
        )

    # Unknown connector types are preserved so renderer can skip unsupported
    # codegen paths instead of failing workflow loading.
    return KnowledgeConnectorSpec(
        connector_type=connector_type,
        endpoint=endpoint,
    )


def _parse_knowledge_search_shared_config(
    data: dict[str, Any],
    path: str,
) -> tuple[KnowledgeConnectorSpec, int, float, Optional[dict[str, Any]]]:
    connector = _parse_knowledge_connector(data.get("connector"), f"{path}.connector")

    max_results = _read_optional_int(data.get("max_results"), f"{path}.max_results")
    if max_results is None:
        max_results = 10
    if max_results < 1:
        raise ValueError(f"{path}.max_results must be >= 1")

    min_score = _read_optional_number(data.get("min_score"), f"{path}.min_score")
    if min_score is None:
        min_score = 0.0
    if min_score < 0.0 or min_score > 1.0:
        raise ValueError(f"{path}.min_score must be between 0.0 and 1.0")

    conditioned_filter_raw = data.get("conditioned_filter")
    knowledge_filter: Optional[dict[str, Any]] = None
    if conditioned_filter_raw is not None:
        knowledge_filter = _expect_dict(conditioned_filter_raw, f"{path}.conditioned_filter")
        knowledge_filter = _parse_json_value(knowledge_filter, f"{path}.conditioned_filter")

    return connector, max_results, min_score, knowledge_filter


def _parse_knowledge_search_tool(raw: Any, path: str) -> KnowledgeSearchToolSpec:
    data = _expect_dict(raw, path)
    connector, max_results, min_score, knowledge_filter = _parse_knowledge_search_shared_config(data, path)

    agentic_filter_info = _parse_agentic_filter_info(data.get("agentic_filter_info"), f"{path}.agentic_filter_info")

    return KnowledgeSearchToolSpec(
        name=_read_optional_string(data.get("name"), f"{path}.name"),
        description=_read_optional_string(data.get("description"), f"{path}.description"),
        connector=connector,
        max_results=max_results,
        min_score=min_score,
        knowledge_filter=knowledge_filter,
        agentic_filter_info=agentic_filter_info,
    )


def _parse_skills_spec(raw: Any, path: str) -> SkillsSpec:
    data = _expect_dict(raw, path)
    roots_raw = data.get("roots")
    if roots_raw is None:
        roots_raw = []
    roots_list = _expect_list(roots_raw, f"{path}.roots")
    roots = tuple(_expect_string(item, f"{path}.roots[{i}]") for i, item in enumerate(roots_list))
    load_mode = _read_optional_string(data.get("load_mode"), f"{path}.load_mode") or "turn"
    return SkillsSpec(roots=roots, load_mode=load_mode)


def _normalize_pcg123_language(raw_language: str, path: str) -> str:
    normalized = raw_language.strip().lower()
    language_map = {
        "python3.8": "python3.8",
        "python38": "python3.8",
        "python3.9": "python3.9",
        "python39": "python3.9",
        "python3.10": "python3.10",
        "python310": "python3.10",
    }
    language = language_map.get(normalized)
    if language is None:
        raise ValueError(f"{path}={raw_language!r} is not supported for pcg123 executor")
    return language


def _parse_local_executor_spec(data: dict[str, Any], path: str) -> ExecutorSpec:
    work_dir = _read_optional_string(data.get("work_dir"), f"{path}.work_dir") or ""
    workspace_mode = _read_optional_string(data.get("workspace_mode"), f"{path}.workspace_mode") or "isolated"
    timeout_seconds = _read_optional_int(data.get("timeout_seconds"), f"{path}.timeout_seconds")
    if timeout_seconds is not None and timeout_seconds < 1:
        raise ValueError(f"{path}.timeout_seconds must be >= 1")
    clean_temp_files = _read_optional_bool(data.get("clean_temp_files"), f"{path}.clean_temp_files")
    return ExecutorSpec(
        type="local",
        work_dir=work_dir,
        workspace_mode=workspace_mode,
        timeout_seconds=timeout_seconds,
        clean_temp_files=clean_temp_files,
    )


def _parse_pcg123_executor_spec(data: dict[str, Any], path: str) -> ExecutorSpec:
    language_raw = _expect_string(data.get("language"), f"{path}.language")
    language = _normalize_pcg123_language(language_raw, f"{path}.language")
    secret_id = _expect_string(data.get("secret_id"), f"{path}.secret_id")
    secret_key = _expect_string(data.get("secret_key"), f"{path}.secret_key")
    execute_timeout_seconds = _read_optional_number(data.get("execute_timeout_seconds"),
                                                    f"{path}.execute_timeout_seconds")
    if execute_timeout_seconds is not None and execute_timeout_seconds < 0:
        raise ValueError(f"{path}.execute_timeout_seconds must be >= 0")
    idle_timeout_seconds = _read_optional_number(data.get("idle_timeout_seconds"), f"{path}.idle_timeout_seconds")
    if idle_timeout_seconds is not None and idle_timeout_seconds < 0:
        raise ValueError(f"{path}.idle_timeout_seconds must be >= 0")
    shared = _read_optional_bool(data.get("shared"), f"{path}.shared")
    interactive = _read_optional_bool(data.get("interactive"), f"{path}.interactive")
    return ExecutorSpec(
        type="pcg123",
        language=language,
        secret_id=secret_id,
        secret_key=secret_key,
        execute_timeout_seconds=execute_timeout_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        shared=shared,
        interactive=interactive,
    )


def _parse_executor_spec(raw: Any, path: str) -> ExecutorSpec:
    data = _expect_dict(raw, path)
    exec_type = _read_optional_string(data.get("type"), f"{path}.type") or "local"
    if exec_type == "local":
        return _parse_local_executor_spec(data, path)
    if exec_type == "pcg123":
        return _parse_pcg123_executor_spec(data, path)
    raise ValueError(f"{path}.type={exec_type!r} is not supported yet (supported: 'local', 'pcg123')")


def _parse_knowledge_config(raw: Any, path: str) -> KnowledgeNodeConfig:
    data = _expect_dict(raw, path)
    query = _expect_string(data.get("query"), f"{path}.query")
    connector, max_results, min_score, knowledge_filter = _parse_knowledge_search_shared_config(data, path)
    return KnowledgeNodeConfig(
        query=query,
        connector=connector,
        max_results=max_results,
        min_score=min_score,
        knowledge_filter=knowledge_filter,
    )


def _parse_llm_config(raw: Any, path: str) -> LLMNodeConfig:
    data = _expect_dict(raw, path)
    model_spec = _parse_model_spec(data.get("model_spec"), f"{path}.model_spec")
    instruction = _read_optional_string(data.get("instruction"), f"{path}.instruction")
    description = _read_optional_string(data.get("description"), f"{path}.description")
    user_message = _read_optional_config_string(data.get("user_message"), f"{path}.user_message")
    temperature = _read_optional_number(data.get("temperature"), f"{path}.temperature")
    max_tokens = _read_optional_int(data.get("max_tokens"), f"{path}.max_tokens")
    top_p = _read_optional_number(data.get("top_p"), f"{path}.top_p")

    output_mode = "text"
    output_schema: Optional[dict[str, Any]] = None
    output_format_raw = data.get("output_format")
    if output_format_raw is not None:
        output_format = _expect_dict(output_format_raw, f"{path}.output_format")
        output_mode = _read_optional_string(output_format.get("type"), f"{path}.output_format.type") or "text"
        if output_mode not in {"text", "json"}:
            raise ValueError(f"{path}.output_format.type must be 'text' or 'json'")
        schema_raw = output_format.get("schema")
        if schema_raw is not None:
            output_schema = _expect_dict(schema_raw, f"{path}.output_format.schema")
    if output_mode == "json" and output_schema is None:
        output_mode = "text"

    mcp_tools: list[MCPToolSpec] = []
    memory_search_tools: list[MemorySearchToolSpec] = []
    knowledge_search_tools: list[KnowledgeSearchToolSpec] = []

    # Legacy field used in existing workflow examples.
    mcp_tools_raw = data.get("mcp_tools", [])
    if mcp_tools_raw is not None:
        for index, item in enumerate(_expect_list(mcp_tools_raw, f"{path}.mcp_tools")):
            mcp_tools.append(_parse_mcp_tool(item, f"{path}.mcp_tools[{index}]"))

    # Unified tools[] format from current schema.
    tools_raw = data.get("tools", [])
    if tools_raw is not None:
        for index, item in enumerate(_expect_list(tools_raw, f"{path}.tools")):
            tool_obj = _expect_dict(item, f"{path}.tools[{index}]")
            tool_type = _read_optional_string(tool_obj.get("type"), f"{path}.tools[{index}].type")
            if tool_type == "mcp":
                mcp_tools.append(_parse_mcp_tool(tool_obj, f"{path}.tools[{index}]"))
            elif tool_type == "memory_search":
                memory_search_tools.append(_parse_memory_search_tool(tool_obj, f"{path}.tools[{index}]"))
            elif tool_type == "knowledge_search":
                knowledge_search_tools.append(_parse_knowledge_search_tool(tool_obj, f"{path}.tools[{index}]"))

    skills_spec: Optional[SkillsSpec] = None
    skills_raw = data.get("skills")
    if skills_raw is not None:
        skills_spec = _parse_skills_spec(skills_raw, f"{path}.skills")

    executor_spec: Optional[ExecutorSpec] = None
    executor_raw = data.get("executor")
    if executor_raw is not None:
        executor_spec = _parse_executor_spec(executor_raw, f"{path}.executor")
        if executor_spec.type == "pcg123" and executor_spec.language != "python3.10":
            raise ValueError(f"{path}.executor.language must be 'python3.10' for skills executor")

    stream = _read_optional_bool(data.get("stream"), f"{path}.stream")

    return LLMNodeConfig(
        model_spec=model_spec,
        instruction=instruction,
        description=description,
        user_message=user_message,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        output_schema=output_schema,
        output_mode=output_mode,
        mcp_tools=tuple(mcp_tools),
        memory_search_tools=tuple(memory_search_tools),
        knowledge_search_tools=tuple(knowledge_search_tools),
        skills=skills_spec,
        executor=executor_spec,
        stream=stream,
    )


def _parse_mcp_config(raw: Any, path: str) -> MCPNodeConfig:
    data = _expect_dict(raw, path)

    remote_raw = data.get("mcp")
    if remote_raw is not None:
        mcp_spec = _parse_mcp_tool(remote_raw, f"{path}.mcp")
    else:
        # Backward compatibility for existing flat MCP config.
        mcp_spec = _parse_mcp_tool(data, path)

    function_name = _read_optional_string(data.get("function"), f"{path}.function")
    if function_name == "":
        function_name = _expect_string(data.get("tool"), f"{path}.tool")

    output_schema_raw = data.get("output_schema")
    output_schema: Optional[dict[str, Any]] = None
    if output_schema_raw is not None:
        output_schema = _expect_dict(output_schema_raw, f"{path}.output_schema")

    return MCPNodeConfig(
        mcp=mcp_spec,
        function=function_name,
        output_schema=output_schema,
    )


def _parse_expression(raw: Any, path: str) -> Expression:
    data = _expect_dict(raw, path)
    expression = _expect_string(data.get("expression"), f"{path}.expression")
    fmt = _read_optional_string(data.get("format"), f"{path}.format") or "cel"
    return Expression(expression=expression, format=fmt)


def _parse_state_variable(raw: Any, path: str) -> StateVariableDefinition:
    data = _expect_dict(raw, path)
    name = _expect_string(data.get("name"), f"{path}.name")
    kind = _read_optional_string(data.get("kind"), f"{path}.kind") or "opaque"
    if kind not in _SUPPORTED_STATE_KINDS:
        supported = ", ".join(sorted(_SUPPORTED_STATE_KINDS))
        raise ValueError(f"{path}.kind={kind!r} is not supported. Supported: {supported}")
    json_schema_raw = data.get("json_schema")
    json_schema: Optional[dict[str, Any]] = None
    if json_schema_raw is not None:
        json_schema = _expect_dict(json_schema_raw, f"{path}.json_schema")
    return StateVariableDefinition(
        name=name,
        kind=kind,
        json_schema=json_schema,
    )


def _split_dot_path(raw_path: str, path: str) -> tuple[str, ...]:
    segments = tuple(segment.strip() for segment in raw_path.split(".") if segment.strip())
    if not segments:
        raise ValueError(f"{path} must reference at least one path segment")
    return segments


def _compile_predicate(expression: str, path: str) -> CompiledPredicate:
    match = _EQ_STRING_PATTERN.match(expression)
    if not match:
        raise ValueError(f"{path} only supports string equality predicates, "
                         "e.g. input.output_parsed.classification == \"math_simple\"")

    left_expr = match.group("left").strip()
    right_raw = match.group("right")
    right_value = ast.literal_eval(right_raw)
    if not isinstance(right_value, str):
        raise ValueError(f"{path} right side must be a string literal")

    if left_expr.startswith(_REF_INPUT_OUTPUT_PARSED + "."):
        segments = _split_dot_path(
            left_expr[len(_REF_INPUT_OUTPUT_PARSED) + 1:],
            f"{path}.left",
        )
        return CompiledPredicate(
            reference=ExpressionReference(root=_REF_INPUT_OUTPUT_PARSED, segments=segments),
            expected_string=right_value,
        )

    if left_expr.startswith(_REF_STATE + "."):
        segments = _split_dot_path(left_expr[len(_REF_STATE) + 1:], f"{path}.left")
        return CompiledPredicate(
            reference=ExpressionReference(root=_REF_STATE, segments=segments),
            expected_string=right_value,
        )

    raise ValueError(f"{path} has unsupported reference root. "
                     "Supported: input.output_parsed.<field>, state.<field>")


def _schema_contains_path(schema: dict[str, Any], segments: tuple[str, ...]) -> bool:
    current: Optional[dict[str, Any]] = schema
    for segment in segments:
        if current is None:
            return False
        properties = current.get("properties")
        if not isinstance(properties, dict):
            return False
        next_schema = properties.get(segment)
        if not isinstance(next_schema, dict):
            return False
        current = next_schema
    return True


def _build_known_state_keys(workflow: WorkflowDefinition) -> set[str]:
    keys = set(_BUILTIN_STATE_KEYS)
    keys.update(variable.name for variable in workflow.state_variables)
    return keys


def _parse_end_config(raw: Any, path: str) -> EndNodeConfig:
    data = _expect_dict(raw, path)
    output_schema_raw = data.get("output_schema")
    output_schema: Optional[dict[str, Any]] = None
    if output_schema_raw is not None:
        output_schema = _expect_dict(output_schema_raw, f"{path}.output_schema")

    expr_raw = data.get("expr")
    expr: Optional[Expression] = None
    if expr_raw is not None:
        expr = _parse_expression(expr_raw, f"{path}.expr")

    return EndNodeConfig(
        output_schema=output_schema,
        expr=expr,
    )


def _parse_transform_config(raw: Any, path: str) -> TransformNodeConfig:
    data = _expect_dict(raw, path)
    output_schema_raw = data.get("output_schema")
    output_schema: Optional[dict[str, Any]] = None
    if output_schema_raw is not None:
        output_schema = _expect_dict(output_schema_raw, f"{path}.output_schema")

    expr_raw = data.get("expr")
    expr: Optional[Expression] = None
    if expr_raw is not None:
        expr = _parse_expression(expr_raw, f"{path}.expr")

    return TransformNodeConfig(
        output_schema=output_schema,
        expr=expr,
    )


def _parse_code_config(raw: Any, path: str) -> CodeNodeConfig:
    data = _expect_dict(raw, path)
    code = _expect_string(data.get("code"), f"{path}.code")
    language = _read_optional_string(data.get("language"), f"{path}.language") or "python"
    legacy_executor_type = _read_optional_string(data.get("executor_type"), f"{path}.executor_type")
    timeout_seconds_raw = _read_optional_int(data.get("timeout"), f"{path}.timeout")
    if timeout_seconds_raw is None:
        timeout_seconds_raw = _read_optional_int(data.get("timeout_seconds"), f"{path}.timeout_seconds")
    if timeout_seconds_raw is not None and timeout_seconds_raw < 1:
        raise ValueError(f"{path}.timeout must be >= 1")

    work_dir_raw = _read_optional_string(data.get("work_dir"), f"{path}.work_dir")
    clean_temp_files_raw = _read_optional_bool(data.get("clean_temp_files"), f"{path}.clean_temp_files")

    executor_spec: ExecutorSpec
    executor_raw = data.get("executor")
    if executor_raw is not None:
        executor_spec = _parse_executor_spec(executor_raw, f"{path}.executor")
        if legacy_executor_type and legacy_executor_type != executor_spec.type:
            raise ValueError(f"{path}.executor_type conflicts with {path}.executor.type")
    else:
        executor_type = legacy_executor_type or "local"
        if executor_type != "local":
            raise ValueError(f"{path}.executor_type={executor_type!r} is not supported yet; "
                             "use config.executor for non-local executors")
        executor_spec = ExecutorSpec(type="local")

    timeout_seconds = timeout_seconds_raw if timeout_seconds_raw is not None else 30
    work_dir = work_dir_raw
    clean_temp_files = True if clean_temp_files_raw is None else clean_temp_files_raw

    if executor_spec.type == "local":
        if executor_spec.timeout_seconds is not None:
            timeout_seconds = executor_spec.timeout_seconds
        if timeout_seconds < 1:
            raise ValueError(f"{path}.timeout_seconds must be >= 1")
        if executor_spec.work_dir:
            work_dir = executor_spec.work_dir
        if executor_spec.clean_temp_files is not None:
            clean_temp_files = executor_spec.clean_temp_files
        executor_spec = replace(
            executor_spec,
            timeout_seconds=timeout_seconds,
            work_dir=work_dir,
            clean_temp_files=clean_temp_files,
        )
    elif executor_spec.type == "pcg123":
        if executor_spec.execute_timeout_seconds is None and timeout_seconds_raw is not None:
            executor_spec = replace(executor_spec, execute_timeout_seconds=float(timeout_seconds_raw))
        normalized_code_language = language.strip().lower()
        if normalized_code_language not in {"python", "python3", "python3.10", "python310"}:
            raise ValueError(f"{path}.language={language!r} is not supported with pcg123 executor")
    else:
        raise ValueError(f"{path}.executor.type={executor_spec.type!r} is not supported yet")

    return CodeNodeConfig(
        code=code,
        language=language,
        executor=executor_spec,
        timeout_seconds=timeout_seconds,
        work_dir=work_dir,
        clean_temp_files=clean_temp_files,
    )


def _parse_user_approval_config(raw: Any, path: str) -> UserApprovalConfig:
    data = _expect_dict(raw, path)
    message = _expect_string(data.get("message"), f"{path}.message")

    routing_data = _expect_dict(data.get("routing"), f"{path}.routing")
    approve_target = _expect_string(routing_data.get("approve"), f"{path}.routing.approve")
    reject_target = _expect_string(routing_data.get("reject"), f"{path}.routing.reject")

    return UserApprovalConfig(
        message=message,
        routing=UserApprovalRouting(
            approve=approve_target,
            reject=reject_target,
        ),
    )


def _parse_set_state_config(raw: Any, path: str) -> SetStateConfig:
    data = _expect_dict(raw, path)
    assignments_raw = _expect_list(data.get("assignments", []), f"{path}.assignments")
    assignments: list[SetStateAssignment] = []
    for index, item in enumerate(assignments_raw):
        assignment_path = f"{path}.assignments[{index}]"
        assignment_obj = _expect_dict(item, assignment_path)
        field_name = _expect_string(assignment_obj.get("field"), f"{assignment_path}.field")
        expr = _parse_expression(assignment_obj.get("expr"), f"{assignment_path}.expr")
        assignments.append(SetStateAssignment(field=field_name, expr=expr))
    return SetStateConfig(assignments=tuple(assignments))


def _parse_node_outputs(raw: Any, path: str) -> tuple[NodeOutputBinding, ...]:
    if raw is None:
        return ()

    outputs_raw = _expect_list(raw, path)
    outputs: list[NodeOutputBinding] = []
    for index, item in enumerate(outputs_raw):
        output_path = f"{path}[{index}]"
        data = _expect_dict(item, output_path)
        name = _expect_string(data.get("name"), f"{output_path}.name")
        output_type = _read_optional_string(data.get("type"), f"{output_path}.type") or "string"
        target = _expect_dict(data.get("target"), f"{output_path}.target")
        target_type = _expect_string(target.get("type"), f"{output_path}.target.type")
        if target_type != "state":
            raise ValueError(f"{output_path}.target.type={target_type!r} is not supported yet (only state)")
        target_field = _expect_string(target.get("field"), f"{output_path}.target.field")
        outputs.append(
            NodeOutputBinding(
                name=name,
                output_type=output_type,
                target_type=target_type,
                target_field=target_field,
            ))
    return tuple(outputs)


def _parse_node(raw: Any, path: str) -> NodeDefinition:
    data = _expect_dict(raw, path)
    node_id = _expect_string(data.get("id"), f"{path}.id")
    node_type = _expect_string(data.get("node_type"), f"{path}.node_type")
    label = _read_optional_string(data.get("label"), f"{path}.label")

    is_custom_node = node_type.startswith("custom.")
    if not is_custom_node and node_type not in SUPPORTED_NODE_TYPES:
        supported = ", ".join(sorted(SUPPORTED_NODE_TYPES))
        raise ValueError(f"{path}.node_type={node_type!r} is not supported yet. Supported: {supported}, custom.*")

    config_raw = data.get("config", {})
    llm_config: Optional[LLMNodeConfig] = None
    end_config: Optional[EndNodeConfig] = None
    transform_config: Optional[TransformNodeConfig] = None
    code_config: Optional[CodeNodeConfig] = None
    mcp_config: Optional[MCPNodeConfig] = None
    knowledge_config: Optional[KnowledgeNodeConfig] = None
    set_state_config: Optional[SetStateConfig] = None
    user_approval_config: Optional[UserApprovalConfig] = None
    if node_type == "builtin.llmagent":
        llm_config = _parse_llm_config(config_raw, f"{path}.config")
    elif node_type == "builtin.end":
        end_config = _parse_end_config(config_raw, f"{path}.config")
    elif node_type == "builtin.transform":
        transform_config = _parse_transform_config(config_raw, f"{path}.config")
    elif node_type == "builtin.code":
        code_config = _parse_code_config(config_raw, f"{path}.config")
    elif node_type == "builtin.mcp":
        mcp_config = _parse_mcp_config(config_raw, f"{path}.config")
    elif node_type == "builtin.knowledge_search":
        knowledge_config = _parse_knowledge_config(config_raw, f"{path}.config")
    elif node_type == "builtin.set_state":
        set_state_config = _parse_set_state_config(config_raw, f"{path}.config")
    elif node_type == "builtin.user_approval":
        user_approval_config = _parse_user_approval_config(config_raw, f"{path}.config")
    else:
        _expect_dict(config_raw, f"{path}.config")

    outputs = _parse_node_outputs(data.get("outputs"), f"{path}.outputs")

    return NodeDefinition(
        node_id=node_id,
        node_type=node_type,
        label=label,
        llm_config=llm_config,
        end_config=end_config,
        transform_config=transform_config,
        code_config=code_config,
        mcp_config=mcp_config,
        knowledge_config=knowledge_config,
        set_state_config=set_state_config,
        user_approval_config=user_approval_config,
        outputs=outputs,
    )


def _parse_edge(raw: Any, path: str) -> EdgeDefinition:
    data = _expect_dict(raw, path)
    source = _expect_string(data.get("source"), f"{path}.source")
    target = _expect_string(data.get("target"), f"{path}.target")
    edge_id = _read_optional_string(data.get("id"), f"{path}.id")
    return EdgeDefinition(source=source, target=target, edge_id=edge_id)


def _parse_conditional_case(raw: Any, path: str) -> ConditionalCase:
    data = _expect_dict(raw, path)
    target = _expect_string(data.get("target"), f"{path}.target")
    predicate = _parse_expression(data.get("predicate"), f"{path}.predicate")
    name = _read_optional_string(data.get("name"), f"{path}.name")
    return ConditionalCase(target=target, predicate=predicate, name=name)


def _parse_conditional_edge(raw: Any, path: str) -> ConditionalEdgeDefinition:
    data = _expect_dict(raw, path)
    from_node = _expect_string(data.get("from"), f"{path}.from")
    edge_id = _read_optional_string(data.get("id"), f"{path}.id")

    condition_raw = _expect_dict(data.get("condition"), f"{path}.condition")
    cases_raw = _expect_list(condition_raw.get("cases"), f"{path}.condition.cases")
    if not cases_raw:
        raise ValueError(f"{path}.condition.cases must contain at least one case")

    cases: list[ConditionalCase] = []
    for index, case_raw in enumerate(cases_raw):
        cases.append(_parse_conditional_case(case_raw, f"{path}.condition.cases[{index}]"))

    default_target = _read_optional_string(condition_raw.get("default"), f"{path}.condition.default")
    return ConditionalEdgeDefinition(
        from_node=from_node,
        condition=ConditionalRule(cases=tuple(cases), default=default_target),
        edge_id=edge_id,
    )


def _validate_workflow_definition(workflow: WorkflowDefinition) -> WorkflowDefinition:
    node_ids = [node.node_id for node in workflow.nodes]
    node_id_set = set(node_ids)
    if len(node_ids) != len(node_id_set):
        raise ValueError("workflow.nodes contains duplicated node ids")

    if workflow.start_node_id not in node_id_set:
        raise ValueError(f"start_node_id {workflow.start_node_id!r} is not present in nodes")

    for edge in workflow.edges:
        if edge.source not in node_id_set:
            raise ValueError(f"edge source {edge.source!r} does not exist")
        if edge.target not in node_id_set and edge.target != "__end__":
            raise ValueError(f"edge target {edge.target!r} does not exist")

    state_variable_names = [variable.name for variable in workflow.state_variables]
    state_variable_name_set = set(state_variable_names)
    if len(state_variable_names) != len(state_variable_name_set):
        raise ValueError("workflow.state_variables contains duplicated names")

    for state_var in workflow.state_variables:
        if state_var.name in _BUILTIN_STATE_KEYS:
            raise ValueError(f"state_variables[{state_var.name!r}] conflicts with built-in state key")

    outgoing_edge_indexes_by_source: dict[str, list[int]] = {}
    for index, edge in enumerate(workflow.edges):
        outgoing_edge_indexes_by_source.setdefault(edge.source, []).append(index)
    normalized_edge_drop_indexes: set[int] = set()
    conditional_sources = {edge.from_node for edge in workflow.conditional_edges}
    resolved_conditional_edges: list[ConditionalEdgeDefinition] = []
    for cond_index, cond in enumerate(workflow.conditional_edges):
        if cond.from_node not in node_id_set:
            raise ValueError(f"conditional edge from={cond.from_node!r} does not exist")

        resolved_cases: list[ConditionalCase] = []
        for case_index, case in enumerate(cond.condition.cases):
            if case.target not in node_id_set and case.target != "__end__":
                raise ValueError(f"conditional edge target {case.target!r} does not exist")
            if case.predicate.format not in {"", "cel"}:
                raise ValueError(
                    f"conditional predicate format {case.predicate.format!r} is not supported; expected 'cel'")
            predicate_path = (
                f"root.conditional_edges[{cond_index}].condition.cases[{case_index}].predicate.expression")
            if case.predicate.expression.strip() == "":
                raise ValueError(f"{predicate_path} must be a non-empty expression")
            resolved_cases.append(case)
        if cond.condition.default and cond.condition.default not in node_id_set and cond.condition.default != "__end__":
            raise ValueError(f"conditional default target {cond.condition.default!r} does not exist")
        resolved_conditional_edges.append(
            replace(
                cond,
                condition=replace(
                    cond.condition,
                    cases=tuple(resolved_cases),
                ),
            ))

    for node in workflow.nodes:
        if node.node_type == "builtin.llmagent":
            if node.llm_config is None:
                raise ValueError(f"llmagent node {node.node_id!r} has no parsed config")
            provider = node.llm_config.model_spec.provider.lower()
            if provider != "openai":
                raise ValueError(
                    f"builtin.llmagent[{node.node_id}] provider {provider!r} is not supported yet (only openai)")
        elif node.node_type == "builtin.end":
            # builtin.end supports config.expr in python codegen.
            # config.output_schema is parsed but ignored by current generator.
            pass
        elif node.node_type == "builtin.transform":
            if node.transform_config is None:
                raise ValueError(f"builtin.transform[{node.node_id}] has no parsed config")
        elif node.node_type == "builtin.code":
            if node.code_config is None:
                raise ValueError(f"builtin.code[{node.node_id}] has no parsed config")
        elif node.node_type == "builtin.mcp":
            if node.mcp_config is None:
                raise ValueError(f"builtin.mcp[{node.node_id}] has no parsed config")
        elif node.node_type == "builtin.knowledge_search":
            if node.knowledge_config is None:
                raise ValueError(f"builtin.knowledge_search[{node.node_id}] has no parsed config")
        elif node.node_type == "builtin.set_state":
            if node.set_state_config is None:
                raise ValueError(f"builtin.set_state[{node.node_id}] has no parsed config")
        elif node.node_type == "builtin.user_approval":
            approval_config = node.user_approval_config
            if approval_config is None:
                raise ValueError(f"builtin.user_approval[{node.node_id}] has no parsed config")
            for target in (approval_config.routing.approve, approval_config.routing.reject):
                if target not in node_id_set and target != "__end__":
                    raise ValueError(f"builtin.user_approval[{node.node_id}] routing target {target!r} does not exist")

            if node.node_id in conditional_sources:
                raise ValueError(f"builtin.user_approval[{node.node_id}] should route by config.routing only, "
                                 "please remove explicit conditional_edges from this node")
            outgoing_indexes = outgoing_edge_indexes_by_source.get(node.node_id, [])
            if outgoing_indexes:
                routing_targets = {approval_config.routing.approve, approval_config.routing.reject}
                direct_targets = {workflow.edges[index].target for index in outgoing_indexes}
                missing_targets = sorted(routing_targets - direct_targets)
                unexpected_targets = sorted(direct_targets - routing_targets)
                if missing_targets or unexpected_targets:
                    raise ValueError(
                        f"builtin.user_approval[{node.node_id}] direct edge targets {sorted(direct_targets)!r} "
                        f"do not match config.routing targets {sorted(routing_targets)!r}; "
                        "please align routing config with edges or remove direct edges from this node")
                normalized_edge_drop_indexes.update(outgoing_indexes)

    normalized_edges = tuple(edge for index, edge in enumerate(workflow.edges)
                             if index not in normalized_edge_drop_indexes)

    return replace(
        workflow,
        edges=normalized_edges,
        conditional_edges=tuple(resolved_conditional_edges),
    )


def _load_workflow_definition_from_payload(payload: Any) -> WorkflowDefinition:
    """Load and validate workflow definition from JSON payload object.

    Supports two JSON shapes:
    - Wrapped: {"root": {"name": "...", "nodes": [], "edges": []}} or
      {"root": {"graph": {"name": "...", "nodes": [], "edges": []}}}
    - Flat: {"name": "...", "nodes": [], "edges": [], "start_node_id": "..."}
    """
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")

    if "root" in payload:
        root_obj = _expect_dict(payload["root"], "root")
        graph_obj = root_obj.get("graph")
        if graph_obj is not None:
            root_obj = _expect_dict(graph_obj, "root.graph")
    elif "name" in payload and "nodes" in payload and "edges" in payload:
        root_obj = payload
    else:
        root_obj = _expect_dict(payload, "root")

    name = _expect_string(root_obj.get("name"), "root.name")
    description = _read_optional_string(root_obj.get("description"), "root.description")
    start_node_id = _expect_string(root_obj.get("start_node_id"), "root.start_node_id")
    version = _read_optional_string(root_obj.get("version"), "root.version")

    nodes_raw = _expect_list(root_obj.get("nodes"), "root.nodes")
    edges_raw = _expect_list(root_obj.get("edges"), "root.edges")
    conditional_edges_raw = _expect_list(root_obj.get("conditional_edges", []), "root.conditional_edges")
    state_variables_raw = _expect_list(root_obj.get("state_variables", []), "root.state_variables")

    nodes: list[NodeDefinition] = []
    for index, raw_node in enumerate(nodes_raw):
        nodes.append(_parse_node(raw_node, f"root.nodes[{index}]"))

    edges: list[EdgeDefinition] = []
    for index, raw_edge in enumerate(edges_raw):
        edges.append(_parse_edge(raw_edge, f"root.edges[{index}]"))

    conditional_edges: list[ConditionalEdgeDefinition] = []
    for index, raw_cond_edge in enumerate(conditional_edges_raw):
        conditional_edges.append(_parse_conditional_edge(raw_cond_edge, f"root.conditional_edges[{index}]"))

    state_variables: list[StateVariableDefinition] = []
    for index, raw_variable in enumerate(state_variables_raw):
        state_variables.append(_parse_state_variable(raw_variable, f"root.state_variables[{index}]"))

    workflow = WorkflowDefinition(
        name=name,
        description=description,
        start_node_id=start_node_id,
        version=version,
        nodes=tuple(nodes),
        edges=tuple(edges),
        conditional_edges=tuple(conditional_edges),
        state_variables=tuple(state_variables),
    )
    return _validate_workflow_definition(workflow)


def load_workflow_definition_from_json_text(workflow_json_text: str) -> WorkflowDefinition:
    """Load and validate workflow definition from raw JSON text."""
    payload = json.loads(workflow_json_text)
    return _load_workflow_definition_from_payload(payload)


def load_workflow_definition(path: str | Path) -> WorkflowDefinition:
    """Load and validate workflow definition from JSON file."""
    json_path = Path(path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    return _load_workflow_definition_from_payload(payload)
