# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Constants for standard converters."""

A2A_DATA_FIELD_CODE_EXECUTION_CODE = "code"
"""Constants for code execution code."""
A2A_DATA_FIELD_CODE_EXECUTION_LANGUAGE = "language"
"""Constants for code execution language."""
A2A_DATA_FIELD_CODE_EXECUTION_OUTCOME = "outcome"
"""Constants for code execution outcome."""
A2A_DATA_FIELD_CODE_EXECUTION_OUTPUT = "output"
"""Constants for code execution output."""
A2A_DATA_FIELD_TOOL_CALL_ARGS = "args"
"""Constants for tool call args."""
A2A_DATA_FIELD_TOOL_CALL_RESPONSE = "response"
"""Constants for tool call response."""
A2A_DATA_PART_METADATA_TYPE_CODE_EXECUTION_RESULT = 'code_execution_result'
"""Constants for code execution result type."""
A2A_DATA_PART_METADATA_TYPE_EXECUTABLE_CODE = 'executable_code'
"""Constants for executable code type."""
A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL = 'function_call'
"""Constants for function call type."""
A2A_DATA_PART_METADATA_TYPE_FUNCTION_RESPONSE = 'function_response'
"""Constants for function response type."""
A2A_DATA_PART_METADATA_TYPE_STREAMING_FUNCTION_CALL_DELTA = 'streaming_function_call_delta'
"""Constants for streaming function call delta type."""
A2A_DATA_PART_METADATA_IS_LONG_RUNNING_KEY = 'is_long_running'
"""Constants for data part metadata is long running key."""
A2A_DATA_PART_METADATA_TYPE_KEY = 'type'
"""Constants for data part metadata type key."""
A2A_DATA_PART_METADATA_IS_LONG_RUNNING_KEY = 'is_long_running'
"""Constants for A2A data part metadata is long running."""

A2A_DATA_PART_METADATA_TYPE_FUNCTION_CALL = 'function_call'
"""Constants for A2A data part metadata type."""

A2A_DATA_PART_METADATA_TYPE_KEY = 'type'
"""Constants for A2A data part metadata type."""

ARTIFACT_ID_SEPARATOR = "-"
"""Constants for artifact id separator."""

DEFAULT_ERROR_MESSAGE = "An error occurred during processing"
"""Constants for default error message."""

# Streaming function call type constants
A2A_DATA_PART_METADATA_TYPE_STREAMING_FUNCTION_CALL = "streaming_function_call"
"""Constants for streaming function call type."""

A2A_DATA_PART_METADATA_TYPE_STREAMING_FUNCTION_CALL_DELTA = "streaming_function_call_delta"
"""Constants for streaming function call delta type."""

A2A_DATA_PART_METADATA_TYPE_CODE_EXECUTION_RESULT = 'code_execution_result'
"""Constants for code execution result type."""

A2A_DATA_PART_METADATA_TYPE_EXECUTABLE_CODE = 'executable_code'
"""Constants for executable code type."""

A2A_DATA_PART_METADATA_TYPE_FUNCTION_RESPONSE = 'function_response'
"""Constants for function response type."""

ARTIFACT_ID_SEPARATOR = "-"
"""Constants for artifact id separator."""
DEFAULT_ERROR_MESSAGE = "An error occurred during processing"
"""Constants for default error message."""
INTERACTION_SPEC_VERSION = "0.1"
"""Constants for interaction spec version."""
MESSAGE_METADATA_INTERACTION_SPEC_VERSION_KEY = "interaction_spec_version"
"""Constants for interaction spec version key."""
MESSAGE_METADATA_OBJECT_TYPE_KEY = "object_type"
"""Constants for message metadata object type key."""
MESSAGE_METADATA_TAG_KEY = "tag"
"""Constants for message metadata tag key."""
MESSAGE_METADATA_RESPONSE_ID_KEY = "llm_response_id"
"""Constants for message metadata response id key."""
EXTENSION_TRPC_A2A_VERSION = "trpc-a2a-version"
"""Constants for extension trpc a2a version key."""

REQUEST_EUC_FUNCTION_CALL_NAME = 'trpc_agent_request_credential'
"""Constants for request euc function call name."""

TRPC_AGENT_CONTEXT_ID_SEPARATOR = "/"
