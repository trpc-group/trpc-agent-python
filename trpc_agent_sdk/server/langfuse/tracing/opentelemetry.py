# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Langfuse OpenTelemetry tracing for TRPC Agent framework."""

import base64
import json
import os
from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import Optional

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export import SpanExporter

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.telemetry import get_trpc_agent_span_name
from trpc_agent_sdk.tools import AGENT_TOOL_APP_NAME_SUFFIX


@dataclass
class LangfuseConfig:
    """Configuration for Langfuse tracing."""

    public_key: Optional[str] = None
    secret_key: Optional[str] = None
    host: Optional[str] = None
    batch_export: bool = True
    compatibility_old_version: bool = False
    """Older version of Langfuse has different span attributes. So they should be setted differently."""
    enable_a2a_trace: bool = False
    """Whether to enable a2a-sdk and HTTP instrumentation traces. Default is False (filter them out)."""


# Global Langfuse configuration, no need to pass as a constructor parameter of _LangfuseMixin
# which will mass up the constructor of SpanProcessor
_langfuse_config: Optional[LangfuseConfig] = None  # pylint: disable=invalid-name


class _LangfuseMixin:
    """Mixin class that provides Langfuse attribute mapping functionality."""

    def _should_skip_span(self, span: ReadableSpan) -> bool:
        """Check if a span should be skipped (not exported to Langfuse).

        Args:
            span: The span to check.

        Returns:
            True if the span should be skipped, False otherwise.
        """
        global _langfuse_config  # pylint: disable=invalid-name
        # If enable_a2a_trace is True, don't filter out any spans
        if _langfuse_config.enable_a2a_trace:
            return False

        if not span.instrumentation_scope:
            return False

        scope_name = span.instrumentation_scope.name
        span_name = span.name

        # Filter out a2a-sdk traces
        if scope_name == 'a2a-python-sdk':
            logger.debug("Skipping a2a-sdk span: %s", span_name)
            return True

        # Filter out OpenTelemetry auto-instrumentation traces
        instrumentation_prefixes = [
            'opentelemetry.instrumentation.httpx',
            'opentelemetry.instrumentation.urllib3',
            'opentelemetry.instrumentation.requests',
            'opentelemetry.instrumentation.asgi',
            'opentelemetry.instrumentation.fastapi',
        ]

        for prefix in instrumentation_prefixes:
            if scope_name.startswith(prefix):
                logger.debug("Skipping OpenTelemetry instrumentation span: %s (scope: %s)", span_name, scope_name)
                return True

        # Filter out HTTP method spans by name
        http_method_prefixes = ['HTTP ', 'GET ', 'POST ', 'PUT ', 'DELETE ', 'PATCH ', 'HEAD ', 'OPTIONS ']
        for prefix in http_method_prefixes:
            if span_name.startswith(prefix):
                logger.debug("Skipping HTTP method span: %s", span_name)
                return True

        return False

    def _transform_span_for_langfuse(self, span: ReadableSpan) -> ReadableSpan:
        """Transform TRPC agent span attributes to Langfuse format."""
        global _langfuse_config  # pylint: disable=invalid-name
        trpc_span_name = get_trpc_agent_span_name()
        if span.name == "invocation":
            span_name = span.attributes.get(f"{trpc_span_name}.runner.name", "unknown")
        else:
            span_name = span.name

        if _langfuse_config.compatibility_old_version:
            langfuse_attributes = self._map_attributes_to_old_langfuse(span.attributes)
        else:
            langfuse_attributes = self._map_attributes_to_langfuse(span.attributes)

        # Fix for nested runner spans: Remove langfuse.trace.name if this is a trpc-agent tool span
        # to avoid overwriting the root trace name (Langfuse only records the first trace.name)
        # AgentTool spans are identified by the AGENT_TOOL_APP_NAME_SUFFIX in the runner.app_name attribute
        app_name = span.attributes.get(f"{trpc_span_name}.runner.app_name", "")
        if app_name and AGENT_TOOL_APP_NAME_SUFFIX in app_name:
            # Remove trace-level attributes to let the root span define the trace
            langfuse_attributes.pop("langfuse.trace.name", None)
            logger.debug("Removed langfuse.trace.name from AgentTool span: %s (app_name: %s)", span_name, app_name)

        transformed_span = ReadableSpan(
            name=span_name,
            context=span.get_span_context(),
            parent=span.parent,
            resource=span.resource,
            attributes=langfuse_attributes,
            events=span.events,
            links=span.links,
            kind=span.kind,
            status=span.status,
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )
        return transformed_span

    def _map_attributes_to_langfuse(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Map TRPC agent attributes to Langfuse-compatible attributes."""
        langfuse_attributes = {}

        # Get the operation name to determine the span type
        operation_name = attributes.get("gen_ai.operation.name", "")

        # Map based on operation type
        if operation_name == "run_runner" or operation_name == "run_runner_cancelled":
            # This is a trace-level span (runner execution or cancelled runner)
            langfuse_attributes.update(self._map_trace_level_attributes(attributes))
        elif operation_name == "run_agent":
            # This is a span observation (agent execution)
            langfuse_attributes.update(self._map_agent_observation_attributes(attributes))
        elif operation_name == "call_llm":
            # This is a generation observation (LLM call)
            langfuse_attributes.update(self._map_generation_attributes(attributes))
        elif operation_name == "execute_tool":
            # This is a span observation (tool execution)
            langfuse_attributes.update(self._map_tool_observation_attributes(attributes))
        else:
            # Default to span observation for unknown operations
            langfuse_attributes.update(self._map_span_observation_attributes(attributes))

        return langfuse_attributes

    def _map_trace_level_attributes(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Map trace-level attributes for runner execution spans."""
        trace_attrs = {}

        # Use TRPC agent span name from trace module
        trpc_span_name = get_trpc_agent_span_name()

        # Map trace name from runner name
        trace_attrs["langfuse.trace.name"] = attributes.get(f"{trpc_span_name}.runner.name", "unknown")

        # Map user ID from runner attributes
        if f"{trpc_span_name}.runner.user_id" in attributes:
            trace_attrs["langfuse.user.id"] = attributes[f"{trpc_span_name}.runner.user_id"]

        # Map session ID from runner attributes
        if f"{trpc_span_name}.runner.session_id" in attributes:
            trace_attrs["langfuse.session.id"] = attributes[f"{trpc_span_name}.runner.session_id"]

        trace_attrs["langfuse.observation.type"] = "span"
        # Map input/output from runner attributes
        if f"{trpc_span_name}.runner.input" in attributes:
            trace_attrs["langfuse.trace.input"] = attributes[f"{trpc_span_name}.runner.input"]
            trace_attrs["langfuse.observation.input"] = trace_attrs["langfuse.trace.input"]

        if f"{trpc_span_name}.runner.output" in attributes:
            trace_attrs["langfuse.trace.output"] = attributes[f"{trpc_span_name}.runner.output"]
            trace_attrs["langfuse.observation.output"] = trace_attrs["langfuse.trace.output"]

        # Map trace metadata from runner attributes
        trace_metadata = {}

        # Map state.begin, state.end, and state.partial to metadata
        if f"{trpc_span_name}.state.begin" in attributes:
            trace_metadata["state_begin"] = attributes[f"{trpc_span_name}.state.begin"]

        if f"{trpc_span_name}.state.end" in attributes:
            trace_metadata["state_end"] = attributes[f"{trpc_span_name}.state.end"]

        if f"{trpc_span_name}.state.partial" in attributes:
            trace_metadata["state_partial"] = attributes[f"{trpc_span_name}.state.partial"]

        # Map cancellation-specific attributes to metadata
        if f"{trpc_span_name}.cancellation.reason" in attributes:
            trace_metadata["cancellation_reason"] = attributes[f"{trpc_span_name}.cancellation.reason"]

        if f"{trpc_span_name}.cancellation.agent_name" in attributes:
            trace_metadata["cancellation_agent_name"] = attributes[f"{trpc_span_name}.cancellation.agent_name"]

        if f"{trpc_span_name}.cancellation.branch" in attributes:
            trace_metadata["cancellation_branch"] = attributes[f"{trpc_span_name}.cancellation.branch"]

        for key, value in attributes.items():
            if key.startswith(f"{trpc_span_name}.runner."):
                # Convert TRPC agent runner attributes to metadata
                clean_key = key.replace(f"{trpc_span_name}.runner.", "")
                trace_metadata[clean_key] = str(value)

        if trace_metadata:
            trace_attrs["langfuse.trace.metadata"] = json.dumps(trace_metadata)

        return trace_attrs

    def _map_agent_observation_attributes(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Map span observation attributes for agent executions."""
        agent_attrs = {}

        # Use TRPC agent span name from trace module
        trpc_span_name = get_trpc_agent_span_name()

        # Set observation type to span
        agent_attrs["langfuse.observation.type"] = "span"

        # Map input/output for agent execution
        agent_attrs["langfuse.observation.input"] = attributes.get(f"{trpc_span_name}.agent.input", "")
        agent_attrs["langfuse.observation.output"] = attributes.get(f"{trpc_span_name}.agent.output", "")

        return agent_attrs

    def _map_generation_attributes(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Map generation observation attributes for LLM calls."""
        gen_attrs = {}

        # Use TRPC agent span name from trace module
        trpc_span_name = get_trpc_agent_span_name()

        # Set observation type to generation
        gen_attrs["langfuse.observation.type"] = "generation"

        # Map input/output for LLM calls
        gen_attrs["langfuse.observation.input"] = attributes.get(f"{trpc_span_name}.llm_request", "unknown")
        gen_attrs["langfuse.observation.output"] = attributes.get(f"{trpc_span_name}.llm_response", "unknown")

        # Map model parameters from LLM request
        llm_request_json = json.loads(attributes.get(f"{trpc_span_name}.llm_request", "{}"))
        config = llm_request_json.get("config", {})
        gen_attrs["langfuse.observation.model.parameters"] = json.dumps(config)

        # Map usage details
        gen_attrs["gen_ai.usage.input_tokens"] = attributes.get("gen_ai.usage.input_tokens", "0")
        gen_attrs["gen_ai.usage.output_tokens"] = attributes.get("gen_ai.usage.output_tokens", "0")

        # Map generation metadata
        gen_metadata = {}
        excluded_keys = {"llm_request", "llm_response", "prompt.name", "prompt.version", "prompt.labels"}
        for key, value in attributes.items():
            if key.startswith(f"{trpc_span_name}."):
                clean_key = key.replace(f"{trpc_span_name}.", "")
                if clean_key not in excluded_keys:
                    gen_metadata[clean_key] = str(value)

        # Instruction-Generation association: attributes written by trace_call_llm()
        # via RemoteInstruction.metadata, mapped to Langfuse native prompt fields.
        instruction_name = attributes.get(f"{trpc_span_name}.instruction.name")
        instruction_version = attributes.get(f"{trpc_span_name}.instruction.version")
        if instruction_name:
            gen_attrs["langfuse.observation.prompt.name"] = instruction_name
        if instruction_version is not None:
            gen_attrs["langfuse.observation.prompt.version"] = instruction_version

        if gen_metadata:
            gen_attrs["langfuse.observation.metadata"] = json.dumps(gen_metadata)

        return gen_attrs

    def _map_tool_observation_attributes(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Map span observation attributes for tool executions."""
        tool_attrs = {}

        # Use TRPC agent span name from trace module
        trpc_span_name = get_trpc_agent_span_name()

        # Set observation type to span
        tool_attrs["langfuse.observation.type"] = "span"

        # Map input/output for tool calls
        tool_attrs["langfuse.observation.input"] = attributes.get(f"{trpc_span_name}.tool_call_args", "unknown")
        tool_attrs["langfuse.observation.output"] = attributes.get(f"{trpc_span_name}.tool_response", "unknown")

        # Map tool-specific metadata
        tool_metadata = {}

        # Map tool name and description
        if "gen_ai.tool.name" in attributes:
            tool_metadata["tool_name"] = attributes["gen_ai.tool.name"]

        if "gen_ai.tool.description" in attributes:
            tool_metadata["tool_description"] = attributes["gen_ai.tool.description"]

        if "gen_ai.tool.call.id" in attributes:
            tool_metadata["tool_call_id"] = attributes["gen_ai.tool.call.id"]

        # Map state.begin and state.end to metadata
        if f"{trpc_span_name}.state.begin" in attributes:
            tool_metadata["state_begin"] = attributes[f"{trpc_span_name}.state.begin"]

        if f"{trpc_span_name}.state.end" in attributes:
            tool_metadata["state_end"] = attributes[f"{trpc_span_name}.state.end"]

        # Map other TRPC agent attributes
        for key, value in attributes.items():
            if key.startswith(f"{trpc_span_name}."):
                clean_key = key.replace(f"{trpc_span_name}.", "")
                # Exclude tool_call_args, tool_response, state.begin,
                # state.end from metadata as they're mapped separately
                if clean_key not in ["tool_call_args", "tool_response", "state.begin", "state.end"]:
                    tool_metadata[clean_key] = str(value)

        if tool_metadata:
            tool_attrs["langfuse.observation.metadata"] = json.dumps(tool_metadata)

        return tool_attrs

    def _map_span_observation_attributes(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Map general span observation attributes for other operations."""
        span_attrs = {}

        # Set observation type to span
        span_attrs["langfuse.observation.type"] = "span"

        span_attrs.update(attributes)

        return span_attrs

    def _map_attributes_to_old_langfuse(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Map attributes to old Langfuse format."""
        langfuse_attributes = {}
        # Get the operation name to determine the span type
        operation_name = attributes.get("gen_ai.operation.name", "")

        # Map based on operation type
        if operation_name == "run_runner" or operation_name == "run_runner_cancelled":
            # This is a trace-level span (runner execution or cancelled runner)
            langfuse_attributes.update(self._map_old_trace_level_attributes(attributes))
        elif operation_name == "run_agent":
            # This is a span observation (agent execution)
            langfuse_attributes.update(self._map_old_agent_observation_attributes(attributes))
        elif operation_name == "call_llm":
            # This is a generation observation (LLM call)
            langfuse_attributes.update(self._map_old_generation_attributes(attributes))
        elif operation_name == "execute_tool":
            # This is a span observation (tool execution)
            langfuse_attributes.update(self._map_old_tool_observation_attributes(attributes))
        else:
            # Default to span observation for unknown operations
            langfuse_attributes.update(self._map_old_span_observation_attributes(attributes))

        return langfuse_attributes

    def _map_old_trace_level_attributes(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Map trace-level attributes for older Langfuse versions."""
        trace_attrs = {}

        # Use TRPC agent span name from trace module
        trpc_span_name = get_trpc_agent_span_name()

        # Map trace name from runner name
        trace_attrs["langfuse.trace.name"] = attributes.get(f"{trpc_span_name}.runner.name", "unknown")

        # Map user ID from runner attributes (old format)
        if f"{trpc_span_name}.runner.user_id" in attributes:
            trace_attrs["user.id"] = attributes[f"{trpc_span_name}.runner.user_id"]

        # Map session ID from runner attributes (old format)
        if f"{trpc_span_name}.runner.session_id" in attributes:
            trace_attrs["session.id"] = attributes[f"{trpc_span_name}.runner.session_id"]

        # Map input/output from runner attributes (old format)
        if f"{trpc_span_name}.runner.input" in attributes:
            trace_attrs["input.value"] = attributes[f"{trpc_span_name}.runner.input"]

        if f"{trpc_span_name}.runner.output" in attributes:
            trace_attrs["output.value"] = attributes[f"{trpc_span_name}.runner.output"]

        # Map trace metadata from runner attributes (old format)
        trace_metadata = {}

        # Map state.begin, state.end, and state.partial to metadata
        if f"{trpc_span_name}.state.begin" in attributes:
            trace_metadata["state_begin"] = attributes[f"{trpc_span_name}.state.begin"]

        if f"{trpc_span_name}.state.end" in attributes:
            trace_metadata["state_end"] = attributes[f"{trpc_span_name}.state.end"]

        if f"{trpc_span_name}.state.partial" in attributes:
            trace_metadata["state_partial"] = attributes[f"{trpc_span_name}.state.partial"]

        # Map cancellation-specific attributes to metadata
        if f"{trpc_span_name}.cancellation.reason" in attributes:
            trace_metadata["cancellation_reason"] = attributes[f"{trpc_span_name}.cancellation.reason"]

        if f"{trpc_span_name}.cancellation.agent_name" in attributes:
            trace_metadata["cancellation_agent_name"] = attributes[f"{trpc_span_name}.cancellation.agent_name"]

        if f"{trpc_span_name}.cancellation.branch" in attributes:
            trace_metadata["cancellation_branch"] = attributes[f"{trpc_span_name}.cancellation.branch"]

        for key, value in attributes.items():
            if key.startswith(f"{trpc_span_name}.runner."):
                # Convert TRPC agent runner attributes to metadata
                clean_key = key.replace(f"{trpc_span_name}.runner.", "")
                trace_metadata[clean_key] = str(value)

        if trace_metadata:
            trace_attrs["langfuse.metadata"] = json.dumps(trace_metadata)

        return trace_attrs

    def _map_old_agent_observation_attributes(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Map span observation attributes for agent executions (older Langfuse versions)."""
        agent_attrs = {}

        # Use TRPC agent span name from trace module
        trpc_span_name = get_trpc_agent_span_name()

        # Map input/output for agent execution (old format)
        agent_attrs["input.value"] = attributes.get(f"{trpc_span_name}.agent.input", "")
        agent_attrs["output.value"] = attributes.get(f"{trpc_span_name}.agent.output", "")

        return agent_attrs

    def _map_old_generation_attributes(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Map generation observation attributes for older Langfuse versions."""
        gen_attrs = {}

        # Use TRPC agent span name from trace module
        trpc_span_name = get_trpc_agent_span_name()

        # Map input/output for LLM calls (old format)
        gen_attrs["gen_ai.prompt"] = attributes.get(f"{trpc_span_name}.llm_request", "unknown")
        gen_attrs["gen_ai.completion"] = attributes.get(f"{trpc_span_name}.llm_response", "unknown")

        # Map model parameters from LLM request (old format)
        llm_request_json = json.loads(attributes.get(f"{trpc_span_name}.llm_request", "{}"))
        config = llm_request_json.get("config", {})
        gen_attrs["gen_ai.request.temperature"] = config.get("temperature", 0.7)
        gen_attrs["gen_ai.request.max_tokens"] = config.get("max_tokens", 1000)
        gen_attrs["gen_ai.request.top_p"] = config.get("top_p", 1.0)

        # Map usage details (old format)
        gen_attrs["gen_ai.usage.input_tokens"] = attributes.get("gen_ai.usage.input_tokens", "0")
        gen_attrs["gen_ai.usage.output_tokens"] = attributes.get("gen_ai.usage.output_tokens", "0")
        gen_attrs["gen_ai.usage.total_tokens"] = attributes.get("gen_ai.usage.total_tokens", "0")

        # Map model name (old format)
        if "gen_ai.request.model" in attributes:
            gen_attrs["gen_ai.request.model"] = attributes["gen_ai.request.model"]
        elif "llm.model_name" in attributes:
            gen_attrs["llm.model_name"] = attributes["llm.model_name"]
        elif "model" in attributes:
            gen_attrs["model"] = attributes["model"]

        # Map generation metadata (old format)
        gen_metadata = {}
        for key, value in attributes.items():
            if key.startswith(f"{trpc_span_name}."):
                clean_key = key.replace(f"{trpc_span_name}.", "")
                # Exclude llm_request and llm_response from metadata as they're mapped separately
                if clean_key not in ["llm_request", "llm_response"]:
                    gen_metadata[clean_key] = str(value)

        if gen_metadata:
            gen_attrs["langfuse.metadata"] = json.dumps(gen_metadata)

        return gen_attrs

    def _map_old_tool_observation_attributes(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Map span observation attributes for older Langfuse versions."""
        tool_attrs = {}

        # Use TRPC agent span name from trace module
        trpc_span_name = get_trpc_agent_span_name()

        # Map input/output for tool calls (old format)
        tool_attrs["input.value"] = attributes.get(f"{trpc_span_name}.tool_call_args", "unknown")
        tool_attrs["output.value"] = attributes.get(f"{trpc_span_name}.tool_response", "unknown")

        # Map tool-specific metadata (old format)
        tool_metadata = {}

        # Map tool name and description
        if "gen_ai.tool.name" in attributes:
            tool_metadata["tool_name"] = attributes["gen_ai.tool.name"]

        if "gen_ai.tool.description" in attributes:
            tool_metadata["tool_description"] = attributes["gen_ai.tool.description"]

        if "gen_ai.tool.call.id" in attributes:
            tool_metadata["tool_call_id"] = attributes["gen_ai.tool.call.id"]

        # Map state.begin and state.end to metadata
        if f"{trpc_span_name}.state.begin" in attributes:
            tool_metadata["state_begin"] = attributes[f"{trpc_span_name}.state.begin"]

        if f"{trpc_span_name}.state.end" in attributes:
            tool_metadata["state_end"] = attributes[f"{trpc_span_name}.state.end"]

        # Map other TRPC agent attributes
        for key, value in attributes.items():
            if key.startswith(f"{trpc_span_name}."):
                clean_key = key.replace(f"{trpc_span_name}.", "")
                # Exclude tool_call_args, tool_response,
                # state.begin, state.end from metadata as they're mapped separately
                if clean_key not in ["tool_call_args", "tool_response", "state.begin", "state.end"]:
                    tool_metadata[clean_key] = str(value)

        if tool_metadata:
            tool_attrs["langfuse.metadata"] = json.dumps(tool_metadata)

        return tool_attrs

    def _map_old_span_observation_attributes(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Map general span observation attributes for older Langfuse versions."""
        span_attrs = {}

        # For old mode, just pass through the attributes
        span_attrs.update(attributes)

        return span_attrs


class _LangfuseSpanProcessor(_LangfuseMixin, SimpleSpanProcessor):
    """Custom span processor that maps TRPC agent traces to Langfuse format."""

    def __init__(self, exporter: SpanExporter):
        super().__init__(exporter)

    def on_end(self, span: ReadableSpan) -> None:
        """Transform span attributes to Langfuse format before exporting."""
        # Skip spans that should not be exported to Langfuse
        if self._should_skip_span(span):
            return

        transformed_span = self._transform_span_for_langfuse(span)
        logger.debug("Langfuse span processor on_end: %s", transformed_span.to_json())
        super().on_end(transformed_span)


class _LangfuseBatchSpanProcessor(_LangfuseMixin, BatchSpanProcessor):
    """Custom batch span processor that maps TRPC agent traces to Langfuse format."""

    def __init__(self, exporter: SpanExporter, **kwargs):
        super().__init__(exporter, **kwargs)

    def on_end(self, span: ReadableSpan) -> None:
        """Transform span attributes to Langfuse format before exporting."""
        # Skip spans that should not be exported to Langfuse
        if self._should_skip_span(span):
            return

        transformed_span = self._transform_span_for_langfuse(span)
        logger.debug("Langfuse batch span processor on_end: %s", transformed_span.to_json())
        super().on_end(transformed_span)


class _LangfuseOTLPExporter(OTLPSpanExporter):
    """Custom OTLP exporter configured for Langfuse."""

    def __init__(self, endpoint: Optional[str] = None, headers: Optional[Dict[str, str]] = None):
        super().__init__(endpoint=endpoint, headers=headers)
        logger.debug("Langfuse OTLP exporter initialized with endpoint: %s and headers: %s", endpoint, headers)

    def export(self, spans) -> "SpanExportResult":
        """Export spans to Langfuse, logging the content before sending."""
        for span in spans:
            logger.debug("=== Exporting span to Langfuse ===\n%s", span.to_json(indent=2))
        return super().export(spans)


def setup(config: Optional[LangfuseConfig] = None) -> TracerProvider:
    """
    Set up OpenTelemetry tracing with Langfuse integration.

    Args:
        config: Langfuse configuration. If None, will try to read from environment variables.

    Returns:
        Configured TracerProvider

    Raises:
        ValueError: If required configuration is missing.
    """
    global _langfuse_config  # pylint: disable=invalid-name

    if config is None:
        config = LangfuseConfig()

        # If public_key or secret_key is None, try to get from environment variables
    if config.public_key is None:
        config.public_key = os.getenv("LANGFUSE_PUBLIC_KEY")

    if config.secret_key is None:
        config.secret_key = os.getenv("LANGFUSE_SECRET_KEY")

    # If host is not specified, try to get from environment variable, otherwise use default
    if config.host is None:
        config.host = os.getenv("LANGFUSE_HOST")

    # Set the global config and use it in span processor(Only be setted once)
    _langfuse_config = config

    # Check if we have the required credentials
    if not config.host or not config.public_key or not config.secret_key:
        raise ValueError("Missing required Langfuse credentials. Please provide public_key and secret_key "
                         "either in the config or set the following environment variables:\n"
                         "export LANGFUSE_PUBLIC_KEY='pk-lf-your-public-key'\n"
                         "export LANGFUSE_SECRET_KEY='sk-lf-your-secret-key'\n"
                         "export LANGFUSE_HOST='https://your-langfuse-host.com'")

    # Create auth string
    auth_string = base64.b64encode(f"{config.public_key}:{config.secret_key}".encode()).decode()

    # Set up endpoint
    config.host = config.host.rstrip("/")
    endpoint = f"{config.host}/api/public/otel/v1/traces"

    # Create exporter
    headers = {"Authorization": f"Basic {auth_string}"}
    exporter = _LangfuseOTLPExporter(endpoint=endpoint, headers=headers)

    # Create span processor
    if config.batch_export:
        span_processor = _LangfuseBatchSpanProcessor(exporter)
    else:
        span_processor = _LangfuseSpanProcessor(exporter)

    # Create and configure tracer provider
    trace_provider = TracerProvider()
    trace_provider.add_span_processor(span_processor)

    # Set as global tracer provider
    trace.set_tracer_provider(trace_provider)

    return trace_provider
