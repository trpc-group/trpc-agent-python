# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Code execution processor for TRPC Agent framework.

This module provides code execution processing capabilities for LLM agents,
including pre-processing requests and post-processing responses to handle
code execution.
"""

from __future__ import annotations

import copy
import dataclasses
import os
import re
from typing import AsyncGenerator
from typing import Optional

from google.genai.types import Outcome
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import CodeExecutionUtils
from trpc_agent_sdk.code_executors import CodeExecutorContext
from trpc_agent_sdk.code_executors import CodeFile
from trpc_agent_sdk.code_executors import ContainerCodeExecutor
from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.events import EventActions
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from .._base_agent import BaseAgent


@dataclasses.dataclass
class DataFileUtil:
    """A structure that contains a data file name and its content."""

    extension: str
    """The file extension (e.g., ".csv")."""

    loader_code_template: str
    """The code template to load the data file."""


_DATA_FILE_UTIL_MAP = {
    "text/csv": DataFileUtil(
        extension=".csv",
        loader_code_template="pd.read_csv('{filename}')",
    ),
}

_DATA_FILE_HELPER_LIB = '''
import pandas as pd

def explore_df(df: pd.DataFrame) -> None:
    """Prints some information about a pandas DataFrame."""

    with pd.option_context(
        'display.max_columns', None, 'display.expand_frame_repr', False
    ):
        # Print the column names to never encounter KeyError when selecting one.
        df_dtypes = df.dtypes

        # Obtain information about data types and missing values.
        df_nulls = (len(df) - df.isnull().sum()).apply(
            lambda x: f'{x} / {df.shape[0]} non-null'
        )

        # Explore unique total values in columns using `.unique()`.
        df_unique_count = df.apply(lambda x: len(x.unique()))

        # Explore unique values in columns using `.unique()`.
        df_unique = df.apply(lambda x: crop(str(list(x.unique()))))

        df_info = pd.concat(
            (
                df_dtypes.rename('Dtype'),
                df_nulls.rename('Non-Null Count'),
                df_unique_count.rename('Unique Values Count'),
                df_unique.rename('Unique Values'),
            ),
            axis=1,
        )
        df_info.index.name = 'Columns'
        print(f"""Total rows: {df.shape[0]}
Total columns: {df.shape[1]}

{df_info}""")
'''


class CodeExecutionRequestProcessor:
    """Processes code execution requests."""

    @staticmethod
    async def run_async(invocation_context: InvocationContext, llm_request: LlmRequest) -> AsyncGenerator[Event, None]:
        """Process code execution requests asynchronously.

        Args:
            invocation_context: The invocation context.
            llm_request: The LLM request to process.

        Yields:
            Events generated during processing.
        """
        if not isinstance(invocation_context.agent, BaseAgent):
            return
        if not invocation_context.agent.code_executor:
            return

        async for event in _run_pre_processor(invocation_context, llm_request):
            yield event

        # Convert the code execution parts to text parts.
        if not isinstance(invocation_context.agent.code_executor, BaseCodeExecutor):
            return
        for content in llm_request.contents:
            CodeExecutionUtils.convert_code_execution_parts(
                content,
                invocation_context.agent.code_executor.code_block_delimiters[-1],
                invocation_context.agent.code_executor.execution_result_delimiters[-1],
            )


class CodeExecutionResponseProcessor:
    """Processes code execution responses."""

    @staticmethod
    async def run_async(invocation_context: InvocationContext,
                        llm_response: LlmResponse) -> AsyncGenerator[Event, None]:
        """Process code execution responses asynchronously.

        Args:
            invocation_context: The invocation context.
            llm_response: The LLM response to process.

        Yields:
            Events generated during processing.
        """
        # Skip if the response is partial (streaming).
        if llm_response.partial:
            return

        async for event in _run_post_processor(invocation_context, llm_response):
            yield event


async def _run_pre_processor(
    invocation_context: InvocationContext,
    llm_request: LlmRequest,
) -> AsyncGenerator[Event, None]:
    """Pre-process the user message by adding data file processing."""
    if not isinstance(invocation_context.agent, BaseAgent):
        return

    agent = invocation_context.agent
    code_executor = agent.code_executor

    if not code_executor or not isinstance(code_executor, BaseCodeExecutor):
        return

    # For container and unsafe local executors, we don't need to process the request
    # as they handle execution directly
    if isinstance(code_executor, (ContainerCodeExecutor, UnsafeLocalCodeExecutor)):
        return

    if not code_executor.optimize_data_file:
        return

    code_executor_context = CodeExecutorContext(invocation_context.session.state)

    # Skip if the error count exceeds the max retry attempts.
    if code_executor_context.get_error_count(invocation_context.invocation_id) >= code_executor.error_retry_attempts:
        return

    # [Step 1] Extract data files from the session_history and store them in
    # memory. Meanwhile, mutate the inline data file to text part in session
    # history from all turns.
    all_input_files = _extract_and_replace_inline_files(code_executor_context, llm_request)

    # [Step 2] Run Explore_Df code on the data files from the current turn. We
    # only need to explore the new data files because the previous data files
    # should already be explored and cached in the code execution runtime.
    processed_file_names = set(code_executor_context.get_processed_file_names())
    files_to_process = [f for f in all_input_files if f.name not in processed_file_names]
    for file in files_to_process:
        code_str = _get_data_file_preprocessing_code(file)
        # Skip for unsupported file or executor types.
        if not code_str:
            return

        # Emit the code to execute, and add it to the LLM request.
        code_content = Content(
            role="model",
            parts=[
                Part(text=f"Processing input file: `{file.name}`"),
                CodeExecutionUtils.build_executable_code_part(code_str),
            ],
        )
        llm_request.contents.append(copy.deepcopy(code_content))
        yield Event(
            invocation_id=invocation_context.invocation_id,
            author=agent.name,
            branch=invocation_context.branch,
            content=code_content,
        )

        code_execution_result = await code_executor.execute_code(
            invocation_context,
            CodeExecutionInput(
                code_blocks=[CodeBlock(language="python", code=code_str)],
                input_files=[file],
                execution_id=_get_or_set_execution_id(invocation_context, code_executor_context),
            ),
        )
        # Update the processing results to code executor context.
        code_executor_context.update_code_execution_result(
            invocation_context.invocation_id,
            [CodeBlock(language="python", code=code_str)],
            code_execution_result,
        )
        code_executor_context.add_processed_file_names([file.name])

        # Emit the execution result, and add it to the LLM request.
        execution_result_event = await _post_process_code_execution_result(invocation_context, code_executor_context,
                                                                           code_execution_result)
        yield execution_result_event
        llm_request.contents.append(copy.deepcopy(execution_result_event.content))


async def _run_post_processor(
    invocation_context: InvocationContext,
    llm_response: LlmResponse,
) -> AsyncGenerator[Event, None]:
    """Post-process the model response by extracting and executing the first code block."""
    agent = invocation_context.agent
    code_executor = agent.code_executor

    if not code_executor or not isinstance(code_executor, BaseCodeExecutor):
        return
    if not llm_response or not llm_response.content:
        return

    # For container and unsafe local executors, we handle execution in post-processing
    if isinstance(code_executor, (ContainerCodeExecutor, UnsafeLocalCodeExecutor)):
        # Continue with post-processing for these executors
        pass

    code_executor_context = CodeExecutorContext(invocation_context.session.state)
    # Skip if the error count exceeds the max retry attempts.
    if code_executor_context.get_error_count(invocation_context.invocation_id) >= code_executor.error_retry_attempts:
        return

    # [Step 1] Extract code from the model predict response and truncate the
    # content to the part with the first code block.
    response_content = llm_response.content
    code_blocks = CodeExecutionUtils.extract_code_and_truncate_content(response_content,
                                                                       code_executor.code_block_delimiters)
    # Terminal state: no code to execute.
    if not code_blocks:
        return

    # [Step 2] Execute the code and generate events
    code_execution_result = await code_executor.execute_code(
        invocation_context,
        CodeExecutionInput(
            code_blocks=code_blocks,
            input_files=code_executor_context.get_input_files(),
            execution_id=_get_or_set_execution_id(invocation_context, code_executor_context),
        ),
    )

    # Update the processing results to code executor context.
    code_executor_context.update_code_execution_result(
        invocation_context.invocation_id,
        code_blocks,
        code_execution_result,
    )

    # Generate events for code execution results
    # Event 1: Code execution event
    parts = [Part.from_executable_code(code=code_block.code, language='PYTHON') for code_block in code_blocks]
    code_execution_event = Event(
        invocation_id=invocation_context.invocation_id,
        author=agent.name,
        branch=invocation_context.branch,
        content=Content(role="model", parts=parts),
        actions=EventActions(),
    )
    yield code_execution_event

    # Event 2: Code execution result event
    result_event = await _post_process_code_execution_result(invocation_context, code_executor_context,
                                                             code_execution_result)
    yield result_event

    # [Step 3] Skip processing the original model response
    # to continue code generation loop.
    llm_response.content = None


def _extract_and_replace_inline_files(
    code_executor_context: CodeExecutorContext,
    llm_request: LlmRequest,
) -> list[CodeFile]:
    """Extracts and replaces inline files with file names in the LLM request."""
    all_input_files = code_executor_context.get_input_files()
    saved_file_names = set(f.name for f in all_input_files)

    # [Step 1] Process input files from LlmRequest and cache them in CodeExecutor.
    for i in range(len(llm_request.contents)):
        content = llm_request.contents[i]
        # Only process the user message.
        if content.role != "user" and not content.parts:
            continue

        for j in range(len(content.parts)):
            part = content.parts[j]
            # Skip if the inline data is not supported.
            if not part.inline_data or part.inline_data.mime_type not in _DATA_FILE_UTIL_MAP:
                continue

            # Replace the inline data file with a file name placeholder.
            mime_type = part.inline_data.mime_type
            file_name = f"data_{i+1}_{j+1}" + _DATA_FILE_UTIL_MAP[mime_type].extension
            llm_request.contents[i].parts[j] = Part(text="\nAvailable file: `%s`\n" % file_name)

            # Add the inline data as input file to the code executor context.
            file = CodeFile(
                name=file_name,
                content=CodeExecutionUtils.get_encoded_file_content(part.inline_data.data).decode(),
                mime_type=mime_type,
            )
            if file_name not in saved_file_names:
                code_executor_context.add_input_files([file])
                all_input_files.append(file)

    return all_input_files


def _get_or_set_execution_id(
    invocation_context: InvocationContext,
    code_executor_context: CodeExecutorContext,
) -> Optional[str]:
    """Returns the ID for stateful code execution or None if not stateful."""
    if not invocation_context.agent.code_executor.stateful:
        return None

    execution_id = code_executor_context.get_execution_id()
    if not execution_id:
        execution_id = invocation_context.session.id
        code_executor_context.set_execution_id(execution_id)
    return execution_id


async def _post_process_code_execution_result(
    invocation_context: InvocationContext,
    code_executor_context: CodeExecutorContext,
    code_execution_result: CodeExecutionResult,
) -> Event:
    """Post-process the code execution result and emit an Event."""

    # Handle code execution error retry.
    is_ok = code_execution_result.outcome == Outcome.OUTCOME_OK
    if not is_ok:
        code_executor_context.increment_error_count(invocation_context.invocation_id)
    else:
        code_executor_context.reset_error_count(invocation_context.invocation_id)

    result_content = Content(
        role="user",
        parts=[
            Part.from_code_execution_result(outcome=code_execution_result.outcome, output=code_execution_result.output),
        ],
    )
    event_actions = EventActions(state_delta=code_executor_context.get_state_delta())

    # Note: Artifact service operations are not supported in TRPC Agent yet.
    # Output files from code execution are not saved as artifacts.

    return Event(
        invocation_id=invocation_context.invocation_id,
        author=invocation_context.agent.name,
        branch=invocation_context.branch,
        content=result_content,
        actions=event_actions,
    )


def _get_data_file_preprocessing_code(file: CodeFile) -> Optional[str]:
    """Returns the code to explore the data file."""

    def _get_normalized_file_name(file_name: str) -> str:
        var_name, _ = os.path.splitext(file_name)
        # Replace non-alphanumeric characters with underscores
        var_name = re.sub(r"[^a-zA-Z0-9_]", "_", var_name)

        # If the filename starts with a digit, prepend an underscore
        if var_name[0].isdigit():
            var_name = "_" + var_name
        return var_name

    if file.mime_type not in _DATA_FILE_UTIL_MAP:
        return

    var_name = _get_normalized_file_name(file.name)
    loader_code = _DATA_FILE_UTIL_MAP[file.mime_type].loader_code_template.format(filename=file.name)
    return f"""
{_DATA_FILE_HELPER_LIB}

# Load the dataframe.
{var_name} = {loader_code}

# Use `explore_df` to guide my analysis.
explore_df({var_name})
"""
