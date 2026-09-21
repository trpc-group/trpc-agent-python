# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# This CodeAct implementation is inspired by and adapted from
# NVIDIA NeMo Labs OO Agents (NOOA):
# https://github.com/NVIDIA-NeMo/labs-OO-Agents
#
# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""LLM-implemented function tools backed by the CodeAct runtime."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import textwrap
from collections.abc import AsyncGenerator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from pydantic import TypeAdapter
from pydantic import create_model
from typing_extensions import override

from trpc_agent_sdk.abc import AgentABC
from trpc_agent_sdk.codeact import BaseCodeActImplementationStore
from trpc_agent_sdk.codeact import CodeActConfig
from trpc_agent_sdk.codeact import CodeActImplementation
from trpc_agent_sdk.codeact import CodeActImplementationPolicy
from trpc_agent_sdk.codeact import InMemoryCodeActImplementationStore
from trpc_agent_sdk.codeact import parse_codeact_result
from trpc_agent_sdk.codeact import prepare_codeact_code
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import new_invocation_context_id
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Outcome
from trpc_agent_sdk.types import Part

from ._base_tool import BaseTool
from ._constants import INPUT_STREAM
from ._constants import TOOL_CONTEXT
from ._function_tool import FunctionTool


@dataclass(frozen=True)
class _CodeActRun:
    result: dict[str, Any]
    code_cells: list[str]


class _ApprovedExecutionAgent(AgentABC):
    """Expose approved-code capabilities without configuring an LLM."""

    tools: list[BaseTool]

    @override
    def get_subagents(self) -> list[AgentABC]:
        return []

    @override
    async def run_async(
        self,
        parent_context: InvocationContext,
    ) -> AsyncGenerator[Any, None]:
        del parent_context
        for event in ():
            yield event


def is_code_act_function(func: Callable[..., Any]) -> bool:
    """Return whether a function body consists of an optional docstring and ``...``."""
    if not (inspect.isfunction(func) or inspect.ismethod(func)):
        return False
    try:
        module = ast.parse(textwrap.dedent(inspect.getsource(func)))
    except (OSError, TypeError, IndentationError, SyntaxError):
        return False

    definition = next(
        (node for node in ast.walk(module)
         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func.__name__),
        None,
    )
    if definition is None:
        return False

    body = definition.body
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return (len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and body[0].value.value is Ellipsis)


class CodeActTool(FunctionTool):
    """Implement an ellipsis-bodied function through a nested CodeAct Agent.

    This class is deliberately opt-in. ``FunctionTool`` remains deterministic;
    use ``CodeActTool`` only when executing model-generated Python is
    intended and the configured CodeAct runtime has an appropriate isolation
    boundary.
    """

    def __init__(
            self,
            func: Callable[..., Any],
            *,
            model: LLMModel | None = None,
            tools: list[BaseTool] | None = None,
            code_act: CodeActConfig | None = None,
            filters: list[BaseFilter | str] | None = None,
            implementation_store: BaseCodeActImplementationStore | None = None,
            implementation_policy: CodeActImplementationPolicy | str = (CodeActImplementationPolicy.DYNAMIC),
    ) -> None:
        if not is_code_act_function(func):
            raise ValueError("CodeActTool requires a function whose body is `...`")
        policy = CodeActImplementationPolicy(implementation_policy)
        if (policy is not CodeActImplementationPolicy.DYNAMIC and implementation_store is None):
            implementation_store = InMemoryCodeActImplementationStore()
        super().__init__(
            func=func,
            filters=filters,
        )
        self.model = model
        self.capability_tools = tools
        self.code_act = code_act
        self.implementation_store = implementation_store
        self.implementation_policy = policy
        self._input_model = self._build_input_model(func)
        self._output_model, self._return_adapter = self._build_output_model(func)
        self._contract_hash = self._build_contract_hash()

    @property
    def contract_hash(self) -> str:
        """Return the hash that invalidates implementations on contract changes."""
        return self._contract_hash

    async def list_implementations(self) -> list[CodeActImplementation]:
        """List generated candidates for the current function contract."""
        if self.implementation_store is None:
            return []
        return await self.implementation_store.list_implementations(
            self.name,
            self.contract_hash,
        )

    async def approve(
        self,
        version: str,
        *,
        approved_by: str | None = None,
    ) -> CodeActImplementation:
        """Promote a candidate. Approving an older version performs rollback."""
        if self.implementation_store is None:
            raise ValueError("CodeActTool has no implementation_store")
        return await self.implementation_store.approve(
            self.name,
            self.contract_hash,
            version,
            approved_by=approved_by,
        )

    @override
    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: dict[str, Any],
    ) -> Any:
        from trpc_agent_sdk.agents import LlmAgent

        validated_input = self._input_model.model_validate(args)
        arguments = validated_input.model_dump(mode="python")

        def get_function_arguments() -> dict[str, Any]:
            """Return the validated arguments for the function being implemented."""
            return arguments

        capabilities = self._resolve_capabilities(tool_context)
        capability_hash = self._build_capability_hash(capabilities)
        approved = await self._get_compatible_approved(capability_hash)
        generated = approved is None
        if (approved is None and self.implementation_policy is CodeActImplementationPolicy.APPROVED_ONLY):
            raise ValueError(f"CodeActTool {self.name!r} has no compatible approved "
                             f"implementation for contract {self.contract_hash}")

        capabilities.insert(0, FunctionTool(get_function_arguments))
        code_act = self.code_act or CodeActConfig()
        if approved is not None:
            result = await self._run_approved_implementation(
                approved=approved,
                arguments=arguments,
                capabilities=capabilities,
                code_act=code_act,
                parent_context=tool_context,
            )
        else:
            model = self.model or getattr(tool_context.agent, "model", None)
            if model is None:
                raise ValueError(f"CodeActTool {self.name!r} requires a model or "
                                 "a model configured on the calling LlmAgent")
            nested_agent = LlmAgent(
                name=f"code_act_{self.name}",
                description=self.description,
                model=model,
                instruction=self._build_instruction(),
                tools=capabilities,
                code_act=code_act,
                input_schema=self._input_model,
                output_schema=self._output_model,
                include_previous_history=False,
            )
            result = await self._run_nested_agent(
                agent=nested_agent,
                arguments=arguments,
                parent_context=tool_context,
            )
        if generated and self.implementation_store is not None:
            implementation = CodeActImplementation.create(
                tool_name=self.name,
                contract_hash=self.contract_hash,
                capability_hash=capability_hash,
                code_cells=result.code_cells,
                metadata={
                    "model": getattr(model, "_model_name",
                                     type(model).__name__),
                },
            )
            await self.implementation_store.save_candidate(implementation)

        value = result.result["result"]
        if self._return_adapter is None:
            return value
        validated = self._return_adapter.validate_python(value)
        if isinstance(validated, BaseModel):
            return validated.model_dump(mode="json")
        return validated

    async def _run_approved_implementation(
        self,
        *,
        approved: CodeActImplementation,
        arguments: dict[str, Any],
        capabilities: list[BaseTool],
        code_act: CodeActConfig,
        parent_context: InvocationContext,
    ) -> _CodeActRun:
        """Execute approved cells directly without an LLM or Runner."""
        execution_agent = _ApprovedExecutionAgent(
            name=f"approved_code_act_{self.name}",
            description=self.description,
            tools=capabilities,
        )
        execution_context = parent_context.model_copy(
            update={
                "invocation_id":
                new_invocation_context_id(),
                "agent":
                execution_agent,
                "branch":
                (f"{parent_context.branch}.{execution_agent.name}" if parent_context.branch else execution_agent.name),
                "user_content":
                Content(
                    role="user",
                    parts=[
                        Part.from_text(text=self._input_model.model_validate(arguments).model_dump_json(
                            exclude_none=True))
                    ],
                ),
            })
        runtime = code_act.runtime
        try:
            execution = await runtime.execute(
                execution_context,
                [prepare_codeact_code(cell) for cell in approved.code_cells],
            )
        finally:
            await runtime.release(execution_context.invocation_id)

        if execution.outcome != Outcome.OUTCOME_OK:
            raise ValueError(f"Approved CodeAct implementation {approved.version!r} "
                             f"failed:\n{execution.output}")
        parsed = parse_codeact_result(
            execution.output,
            self._output_model,
        )
        if not parsed.is_final:
            detail = parsed.validation_error or ("approved implementation did not call return_result")
            raise ValueError(f"Approved CodeAct implementation {approved.version!r} "
                             f"failed: {detail}")
        return _CodeActRun(
            result=self._output_model.model_validate(parsed.value).model_dump(mode="python"),
            code_cells=list(approved.code_cells),
        )

    async def _run_nested_agent(
        self,
        *,
        agent: Any,
        arguments: dict[str, Any],
        parent_context: InvocationContext,
    ) -> _CodeActRun:
        from trpc_agent_sdk.runners import Runner

        app_name = f"{agent.name}_code_act_function"
        sessions = InMemorySessionService()
        runner = Runner(
            app_name=app_name,
            agent=agent,
            artifact_service=parent_context.artifact_service,
            session_service=sessions,
            memory_service=InMemoryMemoryService(),
        )
        session = await sessions.create_session(
            app_name=app_name,
            user_id=parent_context.user_id,
            state=parent_context.state.to_dict(),
        )
        result_text = ""
        code_cells: list[str] = []
        try:
            async for event in runner.run_async(
                    user_id=session.user_id,
                    session_id=session.id,
                    new_message=Content(
                        role="user",
                        parts=[
                            Part.from_text(text=self._input_model.model_validate(arguments).model_dump_json(
                                exclude_none=True))
                        ],
                    ),
                    run_config=parent_context.run_config or RunConfig(),
            ):
                if event.actions.state_delta:
                    parent_context.state.update(event.actions.state_delta)
                if event.content:
                    code_cells.extend(part.executable_code.code or "" for part in event.content.parts
                                      if part.executable_code)
                if event.object == "codeact.result":
                    result_text = event.get_text()
        finally:
            await runner.close()

        if not result_text:
            raise ValueError(f"CodeActTool {self.name!r} produced no CodeAct result")
        return _CodeActRun(
            result=self._output_model.model_validate_json(result_text).model_dump(mode="python"),
            code_cells=code_cells,
        )

    async def _get_compatible_approved(
        self,
        capability_hash: str,
    ) -> CodeActImplementation | None:
        if (self.implementation_store is None or self.implementation_policy is CodeActImplementationPolicy.DYNAMIC):
            return None
        approved = await self.implementation_store.get_approved(
            self.name,
            self.contract_hash,
        )
        if approved is None or approved.capability_hash != capability_hash:
            return None
        return approved

    def _resolve_capabilities(
        self,
        context: InvocationContext,
    ) -> list[BaseTool]:
        if self.capability_tools is not None:
            return list(self.capability_tools)
        parent_tools = getattr(context.agent, "tools", None) or []
        return [tool for tool in parent_tools if tool is not self and getattr(tool, "name", None) != self.name]

    def _build_instruction(self) -> str:
        try:
            signature = inspect.signature(self.func, eval_str=True)
        except (NameError, TypeError, ValueError):
            signature = inspect.signature(self.func)
        docstring = inspect.getdoc(self.func) or "Implement the requested function."
        return "\n".join([
            "Implement the following incomplete Python function for this call.",
            f"Function: {self.name}{signature}",
            f"Contract: {docstring}",
            ("Start by calling "
             "`arguments = await self.get_function_arguments()`."),
            ("Use the argument values and available self capabilities to "
             "compute the result. Do not redefine or invoke the incomplete "
             "function itself."),
            ("Finish with `return_result({'result': value})`, where value "
             "satisfies the annotated return type."),
        ])

    def _build_contract_hash(self) -> str:
        try:
            signature = str(inspect.signature(self.func, eval_str=True))
        except (NameError, TypeError, ValueError):
            signature = str(inspect.signature(self.func))
        payload = {
            "module": getattr(self.func, "__module__", ""),
            "qualname": getattr(self.func, "__qualname__", self.name),
            "signature": signature,
            "docstring": inspect.getdoc(self.func) or "",
            "input_schema": self._input_model.model_json_schema(),
            "output_schema": self._output_model.model_json_schema(),
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()).hexdigest()

    @staticmethod
    def _build_capability_hash(capabilities: list[BaseTool]) -> str:
        declarations: list[dict[str, Any]] = []
        for tool in capabilities:
            declaration = tool._get_declaration()  # pylint: disable=protected-access
            declarations.append({
                "name":
                tool.name,
                "description":
                tool.description,
                "declaration": (declaration.model_dump(mode="json") if declaration is not None else None),
            })
        declarations.sort(key=lambda value: value["name"])
        return hashlib.sha256(
            json.dumps(
                declarations,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()).hexdigest()

    @staticmethod
    def _build_input_model(func: Callable[..., Any], ) -> type[BaseModel]:
        fields: dict[str, tuple[Any, Any]] = {}
        for name, parameter in inspect.signature(func).parameters.items():
            if name in {TOOL_CONTEXT, INPUT_STREAM}:
                continue
            annotation = (Any if parameter.annotation is inspect.Signature.empty else parameter.annotation)
            default = (... if parameter.default is inspect.Signature.empty else parameter.default)
            fields[name] = (annotation, default)
        return create_model(f"{func.__name__.title()}CodeActInput", **fields)

    @staticmethod
    def _build_output_model(func: Callable[..., Any], ) -> tuple[type[BaseModel], TypeAdapter[Any] | None]:
        annotation = inspect.signature(func).return_annotation
        if annotation is inspect.Signature.empty:
            annotation = Any
            adapter = None
        else:
            adapter = TypeAdapter(annotation)
        model = create_model(
            f"{func.__name__.title()}CodeActOutput",
            result=(annotation, ...),
        )
        return model, adapter
