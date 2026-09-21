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
"""Live-object and Agent capability proxies for CodeAct runtimes."""

from __future__ import annotations

import inspect
import json
import uuid
from typing import Any
from typing import TYPE_CHECKING

from pydantic import BaseModel

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.exceptions import RunLimitType

if TYPE_CHECKING:
    from trpc_agent_sdk.tools import BaseTool

_PRIMITIVE_TYPES = (str, int, float, bool, type(None))


class CodeActObjectStore:
    """Invocation-local storage for values kept out of model messages."""

    def __init__(self, *, inline_threshold: int = 256, preview_chars: int = 240) -> None:
        self.inline_threshold = inline_threshold
        self.preview_chars = preview_chars
        self._objects: dict[str, Any] = {}

    def wrap(self, value: Any) -> Any:
        """Return small JSON values inline and retain larger values by reference."""
        if isinstance(value, CodeActObjectRef):
            return value
        if isinstance(value, _PRIMITIVE_TYPES) and (not isinstance(value, str) or len(value) <= self.inline_threshold):
            return value
        if self._can_inline(value):
            return value

        object_id = uuid.uuid4().hex
        self._objects[object_id] = value
        return CodeActObjectRef(self, object_id)

    def unwrap(self, value: Any) -> Any:
        """Resolve references recursively before invoking Python capabilities."""
        if isinstance(value, CodeActObjectRef):
            return value._resolve()  # pylint: disable=protected-access
        if isinstance(value, list):
            return [self.unwrap(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.unwrap(item) for item in value)
        if isinstance(value, dict):
            return {key: self.unwrap(item) for key, item in value.items()}
        return value

    def get(self, object_id: str) -> Any:
        """Resolve an object ID or raise after the invocation has been released."""
        try:
            return self._objects[object_id]
        except KeyError as exc:
            raise ReferenceError(f"CodeAct object reference {object_id!r} is no longer available") from exc

    def clear(self) -> None:
        """Release all invocation-local objects."""
        self._objects.clear()

    def preview(self, value: Any) -> str:
        """Build a bounded representation suitable for model-visible output."""
        try:
            rendered = repr(value)
        except Exception:  # noqa: BLE001  # pylint: disable=broad-except
            rendered = f"<{type(value).__name__}>"
        if len(rendered) > self.preview_chars:
            rendered = rendered[:self.preview_chars] + "..."
        return rendered

    def describe(self, value: Any) -> str:
        """Describe an object without serializing its complete value."""
        details: dict[str, Any] = {
            "type": f"{type(value).__module__}.{type(value).__qualname__}",
            "preview": self.preview(value),
        }
        try:
            details["length"] = len(value)
        except (TypeError, AttributeError):
            pass

        if isinstance(value, BaseModel):
            details["schema"] = value.__class__.model_json_schema()
        else:
            public_names = [name for name in dir(value) if not name.startswith("_")]
            details["public_members"] = public_names[:40]
            if len(public_names) > 40:
                details["members_truncated"] = True
        return json.dumps(details, ensure_ascii=False, default=str)

    def _can_inline(self, value: Any) -> bool:
        try:
            rendered = json.dumps(value, ensure_ascii=False, default=self._reject_json_default)
        except (TypeError, ValueError):
            return False
        return len(rendered) <= self.inline_threshold

    @staticmethod
    def _reject_json_default(value: Any) -> Any:
        raise TypeError(f"{type(value).__name__} is not directly JSON serializable")


class CodeActObjectRef:
    """A bounded, invocation-local proxy for a live Python object."""

    __slots__ = ("_object_id", "_store")

    def __init__(self, store: CodeActObjectStore, object_id: str) -> None:
        self._store = store
        self._object_id = object_id

    @property
    def object_id(self) -> str:
        """Stable ID within the current invocation."""
        return self._object_id

    def describe(self) -> str:
        """Return bounded type, shape, preview, and public-member information."""
        return self._store.describe(self._resolve())

    def _resolve(self) -> Any:
        return self._store.get(self._object_id)

    def __repr__(self) -> str:
        value = self._resolve()
        return (f"ObjectRef(id={self._object_id!r}, type={type(value).__name__!r}, "
                f"preview={self._store.preview(value)!r})")

    def __len__(self) -> int:
        return len(self._resolve())

    def __iter__(self):
        for item in self._resolve():
            yield self._store.wrap(item)

    def __getitem__(self, key: Any) -> Any:
        return self._store.wrap(self._resolve()[self._store.unwrap(key)])

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError("private members are not exposed through ObjectRef")
        member = getattr(self._resolve(), name)
        if not callable(member):
            return self._store.wrap(member)

        def call_member(*args: Any, **kwargs: Any) -> Any:
            result = member(
                *(self._store.unwrap(item) for item in args),
                **{
                    key: self._store.unwrap(item)
                    for key, item in kwargs.items()
                },
            )
            if inspect.isawaitable(result):

                async def await_and_wrap():
                    return self._store.wrap(await result)

                return await_and_wrap()
            return self._store.wrap(result)

        return call_member


class CodeActAgentProxy:
    """Expose only configured Agent tools to generated Python through ``self``."""

    def __init__(self, context: InvocationContext, store: CodeActObjectStore) -> None:
        self._context = context
        self._store = store
        self._tools: dict[str, "BaseTool"] = {}

    async def refresh(self, context: InvocationContext) -> None:
        """Resolve dynamic toolsets for the current invocation."""
        from trpc_agent_sdk.tools import convert_toolunion_to_tool_list

        self._context = context
        resolved = await convert_toolunion_to_tool_list(context.agent.tools, context)
        self._tools = {tool.name: tool for tool in resolved}

    def describe(self) -> str:
        """Return a compact capability catalog; detailed schemas remain on demand."""
        capabilities = [{
            "name": tool.name,
            "description": tool.description or "",
        } for tool in self._tools.values()]
        return json.dumps({"capabilities": capabilities}, ensure_ascii=False)

    def __repr__(self) -> str:
        return f"CodeActAgentProxy(tools={sorted(self._tools)!r})"

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name not in self._tools:
            raise AttributeError(f"CodeAct capability {name!r} is not available")

        tool = self._tools[name]

        async def invoke(*args: Any, **kwargs: Any) -> Any:
            if args:
                raise TypeError(f"self.{name} accepts keyword arguments only")
            if tool.is_progress_streaming:
                raise TypeError(f"self.{name} is progress-streaming and is not supported by CodeAct")
            self._context.raise_if_limit(RunLimitType.MAX_TOOL_CALLS)
            previous_call_id = self._context.function_call_id
            self._context.function_call_id = f"codeact-{uuid.uuid4().hex}"
            try:
                result = await tool.run_async(
                    tool_context=self._context,
                    args={
                        key: self._store.unwrap(value)
                        for key, value in kwargs.items()
                    },
                )
            finally:
                self._context.function_call_id = previous_call_id
            return self._store.wrap(result)

        invoke.__codeact_tool__ = tool  # type: ignore[attr-defined]
        return invoke


def describe_codeact_object(value: Any) -> str:
    """Model-callable ``doc`` helper for proxies, references, and Python values."""
    if isinstance(value, (CodeActAgentProxy, CodeActObjectRef)):
        return value.describe()

    tool = getattr(value, "__codeact_tool__", None)
    if tool is not None:
        details: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description or "",
        }
        declaration = tool._get_declaration()  # pylint: disable=protected-access
        if declaration is not None:
            details["declaration"] = (declaration.model_dump(mode="json", exclude_none=True) if isinstance(
                declaration, BaseModel) else declaration)
        return json.dumps(details, ensure_ascii=False, default=str)

    details: dict[str, Any] = {
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
        "doc": inspect.getdoc(value) or "",
    }
    if callable(value):
        try:
            details["signature"] = str(inspect.signature(value))
        except (TypeError, ValueError):
            pass
    return json.dumps(details, ensure_ascii=False, default=str)
