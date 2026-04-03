# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
"""Claude proxy for TRPC Agent framework."""

import base64
import json
import uuid
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing import Union

import cloudpickle as pickle
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.models import TOOL_STREAMING_ARGS
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Tool

from ._proxy_logger import get_proxy_logger


# Anthropic API Request/Response Models
class ContentBlockText(BaseModel):
    type: Literal["text"]
    text: str


class ContentBlockImage(BaseModel):
    type: Literal["image"]
    source: Dict[str, Any]


class ContentBlockToolUse(BaseModel):
    type: Literal["tool_use"]
    id: str
    name: str
    input: Dict[str, Any]


class ContentBlockToolResult(BaseModel):
    type: Literal["tool_result"]
    tool_use_id: str
    content: Union[str, List[Dict[str, Any]], Dict[str, Any]]


class SystemContent(BaseModel):
    type: Literal["text"]
    text: str


class AnthropicMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: Union[
        str,
        List[Union[
            ContentBlockText,
            ContentBlockImage,
            ContentBlockToolUse,
            ContentBlockToolResult,
        ]],
    ]


class AnthropicTool(BaseModel):
    name: str
    description: Optional[str] = None
    input_schema: Dict[str, Any]


class AnthropicMessagesRequest(BaseModel):
    model: str
    max_tokens: Optional[int] = None
    messages: List[AnthropicMessage]
    system: Optional[Union[str, List[SystemContent]]] = None
    stop_sequences: Optional[List[str]] = None
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    tools: Optional[List[AnthropicTool]] = None
    tool_choice: Optional[Dict[str, Any]] = None


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class AnthropicMessagesResponse(BaseModel):
    id: str
    model: str
    role: Literal["assistant"] = "assistant"
    content: List[Union[ContentBlockText, ContentBlockToolUse]]
    type: Literal["message"] = "message"
    stop_reason: Optional[Literal["end_turn", "max_tokens", "stop_sequence", "tool_use"]] = None
    stop_sequence: Optional[str] = None
    usage: Usage


class TokenCountRequest(BaseModel):
    model: str
    messages: List[AnthropicMessage]
    system: Optional[Union[str, List[SystemContent]]] = None
    tools: Optional[List[AnthropicTool]] = None
    tool_choice: Optional[Dict[str, Any]] = None


class TokenCountResponse(BaseModel):
    input_tokens: int


class AddModelRequest(BaseModel):
    model_data: str  # Base64-encoded pickled model
    config_data: Optional[str] = None  # Base64-encoded pickled GenerateContentConfig


class AddModelResponse(BaseModel):
    model: str  # Model key


class DeleteModelRequest(BaseModel):
    model_key: str  # Model key to delete


class DeleteModelResponse(BaseModel):
    success: bool
    message: str


class AnthropicProxyApp:
    """FastAPI application that proxies Anthropic API requests through LLMModel.

    This class is responsible for:
    - Managing added LLMModel instances
    - Converting between Anthropic API format and internal LlmRequest/LlmResponse
    - Handling both streaming and non-streaming requests

    Lifecycle management (starting/stopping the server) is handled by the global module.
    """

    def __init__(self, claude_models: Optional[Dict[str, Union[LLMModel, Callable[[], Awaitable[LLMModel]]]]] = None):
        """Initialize the proxy server application.

        Args:
            claude_models: Dictionary of default models to pre-register.
                          Values can be either:
                          - LLMModel instances (used directly)
                          - Callable factories returning Awaitable[LLMModel] (called when needed)
                          Keys like "sonnet", "opus", "haiku" will be used for
                          pattern matching against claude-code model names.
                          If a model has generate_content_config, it will be
                          automatically extracted and stored.
        """
        # Get proxy logger instance without setting it as default
        # The subprocess will set it as default when initialized
        self.logger = get_proxy_logger()
        self.models: Dict[str, LLMModel] = {}
        self.model_configs: Dict[str, GenerateContentConfig] = {}  # Map model_key -> config
        self.claude_models: Dict[str, Union[LLMModel,
                                            Callable[[],
                                                     Awaitable[LLMModel]]]] = claude_models if claude_models else {}

        # Extract and store generate_content_config from claude_models if present
        if self.claude_models:
            for model_key, model_or_factory in self.claude_models.items():
                # Only extract config from LLMModel instances, not from factories
                if isinstance(model_or_factory, LLMModel):
                    if hasattr(model_or_factory,
                               'generate_content_config') and model_or_factory.generate_content_config:
                        self.model_configs[model_key] = model_or_factory.generate_content_config
                        self.logger.info("Extracted generate_content_config from model '%s'", model_key)

        self.app = FastAPI(title="Anthropic API Proxy")

        # Register routes
        self._setup_routes()

    async def _resolve_model(self, model_name: str) -> Optional[LLMModel]:
        """Resolve model name to an LLMModel instance.

        First checks for exact match in dynamically added models,
        then checks for pattern match in claude default models
        (e.g., "claude-sonnet-4-20250514" matches "sonnet").

        If the model is a callable factory, it will be invoked to create the model instance.

        Args:
            model_name: The model name from the request

        Returns:
            LLMModel instance or None if not found
        """
        # First try exact match in dynamically added models
        if model_name in self.models:
            self.logger.info("Exact model match in dynamically added models: %s", model_name)
            return self.models[model_name]

        # Try pattern matching for claude-code default models
        model_name_lower = model_name.lower()
        for pattern in ["sonnet", "opus", "haiku"]:
            if pattern in model_name_lower and pattern in self.claude_models:
                self.logger.info("Pattern matched '%s' to claude default model '%s'", model_name, pattern)
                model_or_factory = self.claude_models[pattern]

                # Check if it's a callable factory
                if callable(model_or_factory) and not isinstance(model_or_factory, LLMModel):
                    self.logger.info("Invoking model factory for pattern '%s'", pattern)
                    model = await model_or_factory()

                    # Extract generate_content_config from the created model if present and not already stored
                    if pattern not in self.model_configs:
                        if hasattr(model, 'generate_content_config') and model.generate_content_config:
                            self.model_configs[pattern] = model.generate_content_config
                            self.logger.info("Extracted generate_content_config from factory-created model '%s'",
                                             pattern)

                    return model
                else:
                    return model_or_factory

        return None

    def _setup_routes(self):
        """Set up FastAPI routes."""

        @self.app.post("/v1/messages")
        async def create_message(request: AnthropicMessagesRequest):  # pylint: disable=unused-variable
            try:
                self.logger.info("Processing request: model=%s, stream=%s", request.model, request.stream)

                # Resolve model
                model = await self._resolve_model(request.model)
                if model is None:
                    self.logger.error("Model '%s' not found", request.model)
                    raise ValueError(f"Model '{request.model}' not found.")

                # Convert Anthropic request to LlmRequest
                llm_request = self._convert_anthropic_to_llm_request(request, model)

                # Handle streaming mode
                if request.stream:
                    return StreamingResponse(
                        self._handle_streaming(llm_request, request, model),
                        media_type="text/event-stream",
                    )
                else:
                    # Non-streaming mode
                    response = await self._handle_non_streaming(llm_request, request, model)
                    return response

            except Exception as ex:  # pylint: disable=broad-except
                self.logger.error("Error processing request: %s", ex, exc_info=True)
                raise HTTPException(status_code=500, detail=str(ex))

        @self.app.post("/v1/messages/count_tokens")
        async def count_tokens(request: TokenCountRequest):  # pylint: disable=unused-variable
            try:
                self.logger.info("Token count request: model=%s", request.model)

                # Resolve model
                model = await self._resolve_model(request.model)
                if model is None:
                    self.logger.error("Model '%s' not found", request.model)
                    raise ValueError(f"Model '{request.model}' not found.")

                # Convert to LlmRequest to get approximate token count
                llm_request = self._convert_anthropic_to_llm_request(
                    AnthropicMessagesRequest(
                        model=request.model,
                        messages=request.messages,
                        system=request.system,
                        tools=request.tools,
                        tool_choice=request.tool_choice,
                    ), model)

                # Estimate tokens based on text content
                # This is a simple approximation: ~4 characters per token
                total_chars = 0

                # Count system instruction
                if llm_request.config and llm_request.config.system_instruction:
                    total_chars += len(str(llm_request.config.system_instruction))

                # Count message content
                for content in llm_request.contents:
                    for part in content.parts:
                        if part.text:
                            total_chars += len(part.text)

                # Count tool definitions if present
                if llm_request.config and llm_request.config.tools:
                    for tool in llm_request.config.tools:
                        for func_decl in tool.function_declarations:
                            total_chars += len(func_decl.name or "")
                            total_chars += len(func_decl.description or "")
                            # Rough estimate for schema
                            if func_decl.parameters:
                                total_chars += 100

                # Approximate token count (4 chars per token)
                estimated_tokens = max(1, total_chars // 4)

                return TokenCountResponse(input_tokens=estimated_tokens)

            except Exception as ex:  # pylint: disable=broad-except
                self.logger.error("Error counting tokens: %s", ex, exc_info=True)
                raise HTTPException(status_code=500, detail=str(ex))

        @self.app.post("/add_model")
        async def add_model_endpoint(request: AddModelRequest):  # pylint: disable=unused-variable
            """Add a model via HTTP by unpickling the serialized model data.

            Args:
                request: Contains base64-encoded pickled model data and optional config data

            Returns:
                JSON with model key: {"model": "model-key"}
            """
            try:
                self.logger.info("Received add_model request")

                # Decode base64
                pickled_data = base64.b64decode(request.model_data)

                # Unpickle the model
                model = pickle.loads(pickled_data)

                # Verify it's an LLMModel
                if not isinstance(model, LLMModel):
                    raise HTTPException(status_code=400,
                                        detail=f"Invalid model type: {type(model)}. Expected LLMModel instance.")

                # Generate a 10-character UUID suffix (remove dashes and take first 10 chars)
                uuid_suffix = uuid.uuid4().hex[:10]
                # claude-code always pass model name in lowercase
                model_key = f"{model.name}-{uuid_suffix}".lower()
                self.models[model_key] = model

                # If config_data is provided, unpickle and store it
                if request.config_data:
                    pickled_config = base64.b64decode(request.config_data)
                    config = pickle.loads(pickled_config)
                    self.model_configs[model_key] = config
                    self.logger.info("Config for model %s stored with key: %s", model.name, model_key)

                self.logger.info("Model %s added successfully with key: %s", model.name, model_key)
                return AddModelResponse(model=model_key)

            except Exception as ex:  # pylint: disable=broad-except
                self.logger.error("Error adding model %s: %s", model.name, ex, exc_info=True)
                raise HTTPException(status_code=500, detail=str(ex))

        @self.app.post("/delete_model")
        async def delete_model_endpoint(request: DeleteModelRequest):  # pylint: disable=unused-variable
            """Delete a model from the proxy server.

            Args:
                request: Contains the model key to delete

            Returns:
                JSON with success status: {"success": true, "message": "..."}
            """
            try:
                self.logger.info("Received delete_model request for key: %s", request.model_key)

                # Check if model exists in dynamically added models
                if request.model_key not in self.models:
                    self.logger.warning("Model key '%s' not found in dynamically added models", request.model_key)
                    return DeleteModelResponse(
                        success=False,
                        message=f"Model key '{request.model_key}' not found",
                    )

                # Delete the model
                del self.models[request.model_key]

                # Also delete config if exists
                if request.model_key in self.model_configs:
                    del self.model_configs[request.model_key]
                    self.logger.info("Deleted config for model key: %s", request.model_key)

                self.logger.info("Model with key '%s' deleted successfully", request.model_key)
                return DeleteModelResponse(
                    success=True,
                    message=f"Model '{request.model_key}' deleted successfully",
                )

            except Exception as ex:  # pylint: disable=broad-except
                self.logger.error("Error deleting model: %s", ex, exc_info=True)
                raise HTTPException(status_code=500, detail=str(ex))

        @self.app.get("/")
        async def root():  # pylint: disable=unused-variable
            return {"message": "Anthropic API Proxy Server"}

    def _convert_anthropic_to_llm_request(
        self,
        request: AnthropicMessagesRequest,
        model: LLMModel,
    ) -> LlmRequest:
        """Convert Anthropic request format to LlmRequest.

        Args:
            request: Anthropic API request
            model: The resolved LLMModel instance

        Returns:
            LlmRequest object
        """
        contents = []

        # Convert messages to Content objects
        for msg in request.messages:
            parts = []

            if isinstance(msg.content, str):
                # Simple text message
                parts.append(Part.from_text(text=msg.content))
            else:
                # Complex message with content blocks
                for block in msg.content:
                    if block.type == "text":
                        parts.append(Part.from_text(text=block.text))
                    elif block.type == "image":
                        # Handle image - would need to convert source to inline_data
                        # For now, skip or add placeholder
                        self.logger.warning("Image content not fully supported yet")
                    elif block.type == "tool_use":
                        # Convert tool_use to function_call
                        part = Part.from_function_call(name=block.name, args=block.input)
                        # Set the id if available
                        if block.id:
                            part.function_call.id = block.id
                        parts.append(part)
                    elif block.type == "tool_result":
                        # Convert tool_result to function_response
                        # Note: Anthropic's tool_result doesn't include function name,
                        # so we use a placeholder. The ID is what matters for matching.

                        # Normalize the content to a dictionary format
                        # FunctionResponse requires response to be a dict
                        response_dict = {}
                        if isinstance(block.content, dict):
                            response_dict = block.content
                        elif isinstance(block.content, str):
                            # Wrap string content in a dict
                            response_dict = {"result": block.content}
                        elif isinstance(block.content, list):
                            # Try to extract text from list or convert to dict
                            text_parts = []
                            for item in block.content:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    text_parts.append(item.get("text", ""))
                                elif isinstance(item, str):
                                    text_parts.append(item)
                            response_dict = {"result": "\n".join(text_parts) if text_parts else str(block.content)}
                        else:
                            # Fallback for other types
                            response_dict = {"result": str(block.content)}

                        part = Part.from_function_response(
                            name="tool_response",  # Placeholder name
                            response=response_dict,
                        )
                        # Set the ID to match the tool_use_id
                        part.function_response.id = block.tool_use_id
                        parts.append(part)

            if parts:
                content = Content(parts=parts, role=msg.role)
                contents.append(content)

        # Try to get stored config for this model (by model name/key)
        stored_config = self.model_configs.get(request.model)
        request_fields = request.model_fields_set

        # If no stored config found, try to get config from the model itself
        if not stored_config and hasattr(model, 'generate_content_config') and model.generate_content_config:
            stored_config = model.generate_content_config
            self.logger.debug("Using generate_content_config from model instance for %s", request.model)

        if stored_config:
            # Deep copy the stored config
            config = stored_config.model_copy(deep=True)
            self.logger.debug("Using stored config for model %s", request.model)

            # Set fields from request if they are not already set in the config
            # Only override if the config field is None/not set
            if config.temperature is None and "temperature" in request_fields and request.temperature is not None:
                config.temperature = request.temperature
            if config.max_output_tokens is None and "max_tokens" in request_fields and request.max_tokens is not None:
                config.max_output_tokens = request.max_tokens
            if config.top_p is None and request.top_p is not None:
                config.top_p = request.top_p
            if config.top_k is None and request.top_k is not None:
                config.top_k = request.top_k
            if config.stop_sequences is None and request.stop_sequences is not None:
                config.stop_sequences = request.stop_sequences
        else:
            # No stored config, build from request
            config = GenerateContentConfig()
            if "temperature" in request_fields and request.temperature is not None:
                config.temperature = request.temperature
            if "max_tokens" in request_fields and request.max_tokens is not None:
                config.max_output_tokens = request.max_tokens
            if request.top_p is not None:
                config.top_p = request.top_p
            if request.top_k is not None:
                config.top_k = request.top_k
            if request.stop_sequences is not None:
                config.stop_sequences = request.stop_sequences

        # Add system instruction if present
        if request.system:
            if isinstance(request.system, str):
                config.system_instruction = request.system
            else:
                # Concatenate system content blocks
                system_text = ""
                for block in request.system:
                    if block.type == "text":
                        system_text += block.text + "\n\n"
                config.system_instruction = system_text.strip()

        # Convert tools if present
        if request.tools:
            tools = []
            for anthropic_tool in request.tools:
                # Convert input_schema to Schema
                schema = self._convert_dict_to_schema(anthropic_tool.input_schema)

                # Create FunctionDeclaration
                func_decl = FunctionDeclaration(
                    name=anthropic_tool.name,
                    description=anthropic_tool.description or "",
                    parameters=schema,
                )

                # Create Tool with function_declarations
                tool = Tool(function_declarations=[func_decl])
                tools.append(tool)

            config.tools = tools

        # When streaming is enabled, enable streaming for all tools
        streaming_tool_names = None
        if request.stream and request.tools:
            streaming_tool_names = {t.name for t in request.tools}

        return LlmRequest(contents=contents, config=config, streaming_tool_names=streaming_tool_names)

    def _convert_dict_to_schema(self, schema_dict: Dict[str, Any]) -> Schema:
        """Convert a dictionary schema to Schema object.

        Args:
            schema_dict: Dictionary representation of schema

        Returns:
            Schema object
        """
        schema = Schema()

        if "type" in schema_dict:
            schema.type = schema_dict["type"]

        if "description" in schema_dict:
            schema.description = schema_dict["description"]

        if "properties" in schema_dict:
            schema.properties = {}
            for prop_name, prop_schema in schema_dict["properties"].items():
                schema.properties[prop_name] = self._convert_dict_to_schema(prop_schema)

        if "required" in schema_dict:
            schema.required = schema_dict["required"]

        if "items" in schema_dict:
            schema.items = self._convert_dict_to_schema(schema_dict["items"])

        if "additionalProperties" in schema_dict:
            schema.additional_properties = schema_dict["additionalProperties"]

        return schema

    async def _handle_non_streaming(self, llm_request: LlmRequest, original_request: AnthropicMessagesRequest,
                                    model: LLMModel) -> AnthropicMessagesResponse:
        """Handle non-streaming request.

        Args:
            llm_request: Converted LlmRequest
            original_request: Original Anthropic request
            model: The LLMModel instance to use

        Returns:
            Anthropic response
        """
        # Call model
        response_generator = model.generate_async(llm_request, stream=False)

        # Get the response
        llm_response = None
        async for resp in response_generator:
            llm_response = resp

        if not llm_response:
            self.logger.error("No response from model")
            raise ValueError("No response from model")

        # Check for errors in response
        if llm_response.error_code or llm_response.error_message:
            error_msg = llm_response.error_message or f"Model error: {llm_response.error_code}"
            self.logger.error("Model returned error: %s", error_msg)
            raise HTTPException(status_code=500, detail=error_msg)

        # Convert to Anthropic format
        return self._convert_llm_response_to_anthropic(llm_response, original_request)

    def _convert_llm_response_to_anthropic(self, llm_response: LlmResponse,
                                           original_request: AnthropicMessagesRequest) -> AnthropicMessagesResponse:
        """Convert LlmResponse to Anthropic format.

        Args:
            llm_response: Response from LLMModel
            original_request: Original request for context

        Returns:
            Anthropic-formatted response
        """
        content_blocks = []

        if llm_response.content and llm_response.content.parts:
            for part in llm_response.content.parts:
                if part.text:
                    content_blocks.append(ContentBlockText(type="text", text=part.text))
                elif part.function_call:
                    content_blocks.append(
                        ContentBlockToolUse(
                            type="tool_use",
                            id=part.function_call.id or f"call_{uuid.uuid4().hex[:24]}",
                            name=part.function_call.name,
                            input=part.function_call.args,
                        ))

        # Ensure at least one content block
        if not content_blocks:
            content_blocks.append(ContentBlockText(type="text", text=""))

        # Determine stop reason
        stop_reason = "end_turn"
        if llm_response.error_code:
            if llm_response.error_code == "length":
                stop_reason = "max_tokens"
            elif llm_response.error_code == "tool_calls":
                stop_reason = "tool_use"
        elif content_blocks and any(isinstance(block, ContentBlockToolUse) for block in content_blocks):
            stop_reason = "tool_use"

        # Extract usage
        input_tokens = 0
        output_tokens = 0
        if llm_response.usage_metadata:
            input_tokens = llm_response.usage_metadata.prompt_token_count or 0
            output_tokens = llm_response.usage_metadata.candidates_token_count or 0

        return AnthropicMessagesResponse(
            id=f"msg_{uuid.uuid4().hex[:24]}",
            model=original_request.model,
            role="assistant",
            content=content_blocks,
            stop_reason=stop_reason,
            stop_sequence=None,
            usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        )

    async def _handle_streaming(self, llm_request: LlmRequest, original_request: AnthropicMessagesRequest,
                                model: LLMModel):
        """Handle streaming request.

        Args:
            llm_request: Converted LlmRequest
            original_request: Original Anthropic request
            model: The LLMModel instance to use

        Yields:
            Server-sent events in Anthropic format
        """
        try:
            # Send message_start event
            message_id = f"msg_{uuid.uuid4().hex[:24]}"

            message_data = {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": original_request.model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 0,
                    },
                },
            }
            yield f"event: message_start\ndata: {json.dumps(message_data)}\n\n"

            # Start first content block (text)
            content_block_start_event = {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "text",
                    "text": ""
                },
            }
            yield ("event: content_block_start\n"
                   f"data: {json.dumps(content_block_start_event)}\n\n")

            # Send ping
            yield (f"event: ping\ndata: {json.dumps({'type': 'ping'})}\n\n")

            # Track state
            text_block_closed = False
            last_usage = None
            # Track streaming tool calls: {tool_id: {"index": int, "name": str, "started": bool}}
            streaming_tool_calls: Dict[str, Dict[str, Any]] = {}
            next_tool_index = 0  # Next available tool block index (0 is text block)

            # Call model with streaming
            response_generator = model.generate_async(llm_request, stream=True)

            def _is_streaming_tool_call(resp) -> bool:
                """Check if response is a streaming tool call event."""
                if not resp.partial or not resp.content or not resp.content.parts:
                    return False
                for part in resp.content.parts:
                    if part.function_call:
                        args = part.function_call.args or {}
                        if TOOL_STREAMING_ARGS in args:
                            return True
                return False

            async for llm_response in response_generator:
                # Handle streaming tool call events (partial tool arguments)
                if _is_streaming_tool_call(llm_response):
                    for part in llm_response.content.parts:
                        if part.function_call:
                            tool_id = part.function_call.id or ""
                            tool_name = part.function_call.name
                            args = part.function_call.args or {}
                            delta_json = args.get(TOOL_STREAMING_ARGS, "")

                            # Close text block if not already closed
                            if not text_block_closed:
                                text_block_closed = True
                                yield ("event: content_block_stop\n"
                                       f'data: {json.dumps({"type": "content_block_stop", "index": 0})}\n\n')

                            # Check if we need to start a new tool block
                            if tool_id not in streaming_tool_calls:
                                next_tool_index += 1
                                tool_index = next_tool_index
                                streaming_tool_calls[tool_id] = {
                                    "index": tool_index,
                                    "name": tool_name,
                                    "started": True,
                                }

                                # Send content_block_start for tool_use
                                tool_start_event = {
                                    "type": "content_block_start",
                                    "index": tool_index,
                                    "content_block": {
                                        "type": "tool_use",
                                        "id": tool_id or f"toolu_{uuid.uuid4().hex[:24]}",
                                        "name": tool_name,
                                        "input": {},
                                    },
                                }
                                yield ("event: content_block_start\n"
                                       f"data: {json.dumps(tool_start_event)}\n\n")

                            # Send delta JSON
                            if delta_json:
                                tool_index = streaming_tool_calls[tool_id]["index"]
                                tool_delta_event = {
                                    "type": "content_block_delta",
                                    "index": tool_index,
                                    "delta": {
                                        "type": "input_json_delta",
                                        "partial_json": delta_json
                                    },
                                }
                                yield ("event: content_block_delta\n"
                                       f"data: {json.dumps(tool_delta_event)}\n\n")

                # Handle partial responses (streaming text only)
                elif llm_response.partial and llm_response.content:
                    for part in llm_response.content.parts:
                        if part.text and not text_block_closed:
                            # Stream text deltas
                            text_delta_event = {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {
                                    "type": "text_delta",
                                    "text": part.text
                                },
                            }
                            yield ("event: content_block_delta\n"
                                   f"data: {json.dumps(text_delta_event)}\n\n")

                # Handle final complete response (text + function calls)
                elif not llm_response.partial:
                    # Close text block first
                    if not text_block_closed:
                        text_block_closed = True
                        yield ("event: content_block_stop\n"
                               f'data: {json.dumps({"type": "content_block_stop", "index": 0})}\n\n')

                    # Close any streaming tool blocks that were started
                    for tool_id, tool_info in streaming_tool_calls.items():
                        if tool_info.get("started") and not tool_info.get("closed"):
                            tool_info["closed"] = True
                            yield (
                                "event: content_block_stop\n"
                                f'data: {json.dumps({"type": "content_block_stop", "index": tool_info["index"]})}\n\n')

                    # Process function calls from final response (only those not already streamed)
                    if llm_response.content and llm_response.content.parts:
                        for part in llm_response.content.parts:
                            if part.function_call:
                                tool_id = part.function_call.id or ""

                                # Skip if this tool was already handled via streaming
                                if tool_id in streaming_tool_calls:
                                    continue

                                # Tool calls that weren't streamed - send complete
                                next_tool_index += 1
                                tool_index = next_tool_index
                                final_tool_id = tool_id or f"toolu_{uuid.uuid4().hex[:24]}"

                                # Start tool use block
                                tool_start_event = {
                                    "type": "content_block_start",
                                    "index": tool_index,
                                    "content_block": {
                                        "type": "tool_use",
                                        "id": final_tool_id,
                                        "name": part.function_call.name,
                                        "input": {},
                                    },
                                }
                                yield ("event: content_block_start\n"
                                       f"data: {json.dumps(tool_start_event)}\n\n")

                                # Send complete tool input as JSON
                                args_json = json.dumps(part.function_call.args)
                                tool_delta_event = {
                                    "type": "content_block_delta",
                                    "index": tool_index,
                                    "delta": {
                                        "type": "input_json_delta",
                                        "partial_json": args_json
                                    },
                                }
                                yield ("event: content_block_delta\n"
                                       f"data: {json.dumps(tool_delta_event)}\n\n")

                                # Close tool block
                                yield ("event: content_block_stop\n"
                                       f'data: {json.dumps({"type": "content_block_stop", "index": tool_index})}\n\n')

                    # Get usage
                    if llm_response.usage_metadata:
                        last_usage = llm_response.usage_metadata

                    # Determine stop reason
                    stop_reason = "end_turn"
                    if llm_response.content:
                        has_tool_calls = any(part.function_call for part in llm_response.content.parts)
                        if has_tool_calls:
                            stop_reason = "tool_use"

                    # Also check streaming tool calls
                    if streaming_tool_calls:
                        stop_reason = "tool_use"

                    if llm_response.error_code == "length":
                        stop_reason = "max_tokens"

                    # Send message_delta with stop reason and usage
                    output_tokens = last_usage.candidates_token_count if last_usage else 0
                    message_delta_event = {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": stop_reason,
                            "stop_sequence": None
                        },
                        "usage": {
                            "output_tokens": output_tokens
                        },
                    }
                    yield ("event: message_delta\n"
                           f"data: {json.dumps(message_delta_event)}\n\n")

                    # Send message_stop
                    yield f'event: message_stop\ndata: {json.dumps({"type": "message_stop"})}\n\n'

                    # Send [DONE]
                    yield "data: [DONE]\n\n"

        except Exception as ex:  # pylint: disable=broad-except
            self.logger.error("Error in streaming: %s", str(ex))
            # Send error response to client
            error_data = {
                "type": "message_delta",
                "delta": {
                    "stop_reason": "error",
                    "stop_sequence": None,
                    "error_detail": str(ex),  # Include error details
                },
                "usage": {
                    "output_tokens": 0
                },
            }
            yield ("event: message_delta\n"
                   f"data: {json.dumps(error_data)}\n\n")
            yield f'event: message_stop\ndata: {json.dumps({"type": "message_stop"})}\n\n'
            yield "data: [DONE]\n\n"
