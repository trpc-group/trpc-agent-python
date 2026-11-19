# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import asyncio
import base64
import json
import uuid
from pathlib import Path

import pytest
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.agents.dsh import DSHAgent
from trpc_agent_sdk.server.agents.dsh import DSHConfig
from trpc_agent_sdk.server.agents.dsh import DSHModel
from trpc_agent_sdk.server.agents.dsh import DSHRunInput
from trpc_agent_sdk.server.agents.dsh._agent import _default_content_blocks
from trpc_agent_sdk.server.agents.dsh._protocol import DSH_SESSION_STATE_KEY
from trpc_agent_sdk.server.agents.dsh._protocol import Frame
from trpc_agent_sdk.server.agents.dsh._runtime import DSHRuntime
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Blob
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FileData
from trpc_agent_sdk.types import Part


class _FakeRuntime:

    def __init__(self):
        self.initialized = False
        self.requests = []
        self.turn = 0
        self.runtime_cwd = Path.cwd()

    async def init(self):
        self.initialized = True

    async def destroy(self):
        self.initialized = False

    @staticmethod
    def _create_jsonl(run_input):
        header = {
            "type": "session",
            "version": run_input.version,
            "id": run_input.session_id,
            "createdAt": 1,
            "isSeeded": False,
            "delegationDepth": 0,
        }
        if run_input.cwd:
            header["cwd"] = run_input.cwd
        return json.dumps(header) + "\n"

    async def run(self, run_input, *, cancel_event=None):
        self.requests.append(run_input)
        self.turn += 1
        text = "first" if self.turn == 1 else "second"

        # The real runtime creates the first snapshot through agents.create()
        # and returns it in the prompt receipt before any live event arrives.
        jsonl = run_input.jsonl or self._create_jsonl(run_input)
        yield Frame("snapshot", {"jsonl": jsonl, "version": run_input.version})

        next_seq = max(0, len([line for line in jsonl.splitlines() if line.strip()]) - 1)
        yield Frame("delta", {"kind": "text", "text": text})
        yield Frame("event", {
            "type": "assistant/message",
            "seq": next_seq,
            "data": {"message": {"content": [{"type": "text", "text": text}]}},
        })
        yield Frame("status", {"status": "idle"})


def _text(text: str) -> Content:
    return Content(parts=[Part(text=text)])


def _dsh_conf(tmp_path, **model_fields):
    """Build a test DSHConfig with an OpenAI model by default."""

    provider = model_fields.pop("provider", "openai")
    model_name = model_fields.pop("model", "test-model")
    return DSHConfig(
        profile="test",
        dsh_home=str(tmp_path),
        model=DSHModel(provider=provider, model=model_name, **model_fields),
    )


def _partial_text(events):
    chunks = []
    for event in events:
        if not event.partial or not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.text and not part.thought:
                chunks.append(part.text)
    return "".join(chunks)


async def _run_turn(agent, new_message, session_service, session_id, run_config=None):
    runner = Runner(app_name="demo", agent=agent, session_service=session_service)
    kwargs = {}
    if run_config is not None:
        kwargs["run_config"] = run_config
    events = [
        event async for event in runner.run_async(
            user_id="u1",
            session_id=session_id,
            new_message=new_message,
            **kwargs,
        )
    ]
    session = await session_service.get_session(
        app_name="demo", user_id="u1", session_id=session_id)
    return events, session


async def _run_single_turn(agent, new_message, run_config=None):
    session_service = InMemorySessionService()
    session_id = str(uuid.uuid4())
    await session_service.create_session(
        app_name="demo",
        user_id="u1",
        session_id=session_id,
    )
    return await _run_turn(agent, new_message, session_service, session_id, run_config)


def test_run_input_maps_directly_to_prompt_params():
    run_input = DSHRunInput(
        session_id="s1",
        content_blocks=[{"type": "text", "text": "hi"}],
        cwd="/workspace/alice",
        jsonl='{"type":"session"}\n',
        version=4,
        model={"provider": "openai", "model": "m"},
        meta={"tenant": "a"},
    )
    assert run_input.to_params() == {
        "sessionId": "s1",
        "contentBlocks": [{"type": "text", "text": "hi"}],
        "cwd": "/workspace/alice",
        "jsonl": '{"type":"session"}\n',
        "version": 4,
        "model": {"provider": "openai", "model": "m"},
        "meta": {"tenant": "a"},
    }


def test_run_input_omits_missing_cwd():
    run_input = DSHRunInput(
        session_id="s1",
        content_blocks=[{"type": "text", "text": "hi"}],
        jsonl="",
        version=4,
    )
    assert "cwd" not in run_input.to_params()


def test_default_content_blocks_convert_text_image_and_file():
    image = b"\x89PNG\r\n\x1a\n"
    file = b"%PDF-1.4\n"
    message = Content(parts=[
        Part(text="describe"),
        Part(inline_data=Blob(data=image, mime_type="image/png")),
        Part(inline_data=Blob(data=file, mime_type="application/pdf")),
    ])

    blocks = _default_content_blocks(message, allow_unconverted=False)

    assert blocks[0] == {"type": "text", "text": "describe"}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["mimeType"] == "image/png"
    assert base64.b64decode(blocks[1]["data"]) == image
    assert blocks[2]["type"] == "file"
    assert base64.b64decode(blocks[2]["data"]) == file


def test_default_content_blocks_use_part_name():
    message = Content(parts=[
        Part(
            inline_data=Blob(data=b"%PDF-1.4\n", mime_type="application/pdf"),
            part_metadata={"name": "report.pdf"},
        ),
    ])

    blocks = _default_content_blocks(message, allow_unconverted=False)
    assert blocks[0]["name"] == "report.pdf"


def test_file_data_requires_custom_dsh_input():
    message = Content(parts=[
        Part(file_data=FileData(file_uri="file:///tmp/report.pdf", mime_type="application/pdf")),
    ])

    with pytest.raises(ValueError, match="file_data"):
        _default_content_blocks(message, allow_unconverted=False)
    assert _default_content_blocks(message, allow_unconverted=True) == []


def test_two_turns_persist_jsonl_and_meta(tmp_path):
    async def scenario():
        agent = DSHAgent(
            name="dsh_agent",
            dsh_conf=_dsh_conf(tmp_path, base_url="http://localhost", api_key="key"),
        )
        runtime = _FakeRuntime()
        agent._runtime = runtime
        await agent.init()
        assert agent.dsh_conf.model.to_spec() == {
            "provider": "openai", "model": "test-model"}

        session_service = InMemorySessionService()
        session_id = "s1"
        await session_service.create_session(
            app_name="demo",
            user_id="u1",
            session_id=session_id,
        )

        first_events, session = await _run_turn(
            agent, _text("first question"), session_service, session_id)
        assert _partial_text(first_events) == "first"
        assert DSH_SESSION_STATE_KEY in session.state
        assert '"text":"first"' in session.state[DSH_SESSION_STATE_KEY]["jsonl"]
        assert runtime.requests[0].jsonl == ""
        assert runtime.requests[0].cwd == str(runtime.runtime_cwd)

        second_events, session = await _run_turn(
            agent,
            _text("second question"),
            session_service,
            session_id,
            run_config=RunConfig(custom_data={"tenant_id": "tenant-a"}),
        )
        assert _partial_text(second_events) == "second"
        assert len(runtime.requests) == 2
        assert runtime.requests[1].jsonl is not None
        assert runtime.requests[1].jsonl != ""
        assert '"text":"first"' in runtime.requests[1].jsonl
        assert runtime.requests[1].meta == {"tenant_id": "tenant-a"}
        assert runtime.requests[1].model == {"provider": "openai", "model": "test-model"}

        assert '"text":"first"' in session.state[DSH_SESSION_STATE_KEY]["jsonl"]
        assert '"text":"second"' in session.state[DSH_SESSION_STATE_KEY]["jsonl"]

    asyncio.run(scenario())


def test_inline_image_is_sent_to_runtime(tmp_path):
    async def scenario():
        image = b"\x89PNG\r\n\x1a\n"
        agent = DSHAgent(
            name="dsh_agent",
            dsh_conf=_dsh_conf(tmp_path),
        )
        runtime = _FakeRuntime()
        agent._runtime = runtime
        await agent.init()

        message = Content(parts=[
            Part(text="describe"),
            Part(inline_data=Blob(data=image, mime_type="image/png")),
        ])
        await _run_single_turn(agent, message)

        blocks = runtime.requests[0].content_blocks
        assert blocks[0] == {"type": "text", "text": "describe"}
        assert blocks[1]["type"] == "image"
        assert base64.b64decode(blocks[1]["data"]) == image

    asyncio.run(scenario())


def test_model_none_lets_profile_choose(tmp_path):
    async def scenario():
        agent = DSHAgent(
            name="dsh_agent",
            dsh_conf=DSHConfig(profile="test", dsh_home=str(tmp_path)),
        )
        runtime = _FakeRuntime()
        agent._runtime = runtime
        await agent.init()
        assert agent.dsh_conf.model is None

        await _run_single_turn(agent, _text("question"))
        assert runtime.requests[0].model is None

    asyncio.run(scenario())


def test_dsh_model_to_spec():
    model = DSHModel(
        provider="openai",
        model="deepseek-chat",
        reasoning_effort="high",
        max_tokens=2048,
        base_url="http://gateway",
        api_key="key",
    )
    assert model.to_spec() == {
        "provider": "openai",
        "model": "deepseek-chat",
        "reasoningEffort": "high",
        "maxTokens": 2048,
    }


@pytest.mark.parametrize("kwargs", [
    {"provider": "", "model": "m"},
    {"provider": "openai", "model": ""},
    {"provider": "openai", "model": "m", "reasoning_effort": ""},
    {"provider": "openai", "model": "m", "max_tokens": 0},
])
def test_dsh_model_validation(kwargs):
    with pytest.raises(ValueError):
        DSHModel(**kwargs)


def test_model_route_supports_openai(tmp_path):
    runtime = DSHRuntime(_dsh_conf(
        tmp_path,
        model="deepseek-chat",
        max_tokens=1024,
        base_url="http://gateway",
        api_key="key",
    ))
    assert runtime.env["DEEPSEEK_BASE_URL"] == "http://gateway"
    assert runtime.env["DEEPSEEK_API_KEY"] == "key"
    assert runtime._model_route() == {
        "id": "llm-pi-ai",
        "config": {
            "providers": {
                "openai": {
                    "api": "openai-completions",
                    "baseURL": "http://gateway",
                    "apiKeyEnv": "DEEPSEEK_API_KEY",
                    "models": [{"id": "deepseek-chat", "maxTokens": 1024}],
                },
            },
        },
    }


def test_model_route_supports_anthropic(tmp_path):
    runtime = DSHRuntime(DSHConfig(
        profile="test",
        dsh_home=str(tmp_path),
        model=DSHModel(
            provider="anthropic",
            model="claude-sonnet-4",
            max_tokens=8192,
            base_url="http://anthropic",
            api_key="key",
        ),
    ))
    assert runtime._model_route() == {
        "id": "llm-pi-ai",
        "config": {
            "providers": {
                "anthropic": {
                    "api": "anthropic-messages",
                    "baseURL": "http://anthropic",
                    "apiKeyEnv": "DEEPSEEK_API_KEY",
                    "models": [{"id": "claude-sonnet-4", "maxTokens": 8192}],
                },
            },
        },
    }


def test_model_route_leaves_unknown_provider_to_profile(tmp_path):
    runtime = DSHRuntime(DSHConfig(
        profile="test",
        dsh_home=str(tmp_path),
        model=DSHModel(provider="custom", model="m"),
    ))
    assert runtime._model_route() is None


def test_model_route_is_none_without_model(tmp_path):
    runtime = DSHRuntime(DSHConfig(profile="test", dsh_home=str(tmp_path)))
    assert runtime._model_route() is None


def test_dsh_config_is_mutable():
    config = DSHConfig(profile="test", dsh_home="/tmp")
    config.model = DSHModel(provider="openai", model="changed")
    assert config.model.model == "changed"


def test_dsh_home_falls_back_to_config_env(tmp_path, monkeypatch):
    monkeypatch.delenv("DSH_HOME", raising=False)
    runtime = DSHRuntime(DSHConfig(
        profile="test",
        env={"DSH_HOME": str(tmp_path)},
    ))
    assert runtime.dsh_home == tmp_path.resolve()


def test_missing_dsh_home_raises(monkeypatch):
    monkeypatch.delenv("DSH_HOME", raising=False)
    with pytest.raises(ValueError, match="dsh_home"):
        DSHAgent(
            name="dsh_agent",
            dsh_conf=DSHConfig(profile="test"),
        )


def test_custom_dsh_input_can_handle_file_data(tmp_path):
    async def scenario():
        async def custom_dsh_input(ctx, run_input):
            run_input.content_blocks.append({
                "type": "file",
                "attachment": {
                    "attachmentId": "file-1",
                    "name": "report.pdf",
                    "bytes": 3,
                },
            })
            return run_input

        agent = DSHAgent(
            name="dsh_agent",
            dsh_conf=_dsh_conf(tmp_path),
            custom_dsh_input=custom_dsh_input,
        )
        runtime = _FakeRuntime()
        agent._runtime = runtime
        await agent.init()

        message = Content(parts=[
            Part(file_data=FileData(file_uri="file:///tmp/report.pdf", mime_type="application/pdf")),
        ])
        await _run_single_turn(agent, message)

        block = runtime.requests[0].content_blocks[0]
        assert block["type"] == "file"
        assert block["attachment"]["name"] == "report.pdf"

    asyncio.run(scenario())


def test_custom_dsh_input_changes_cwd(tmp_path):
    async def scenario():
        session_cwd = tmp_path / "workspace"
        session_cwd.mkdir()

        async def custom_dsh_input(ctx, run_input):
            run_input.cwd = str(session_cwd)
            return run_input

        agent = DSHAgent(
            name="dsh_agent",
            dsh_conf=_dsh_conf(tmp_path),
            custom_dsh_input=custom_dsh_input,
        )
        runtime = _FakeRuntime()
        agent._runtime = runtime
        await agent.init()

        _, session = await _run_single_turn(agent, _text("question"))
        assert runtime.requests[0].jsonl == ""
        assert runtime.requests[0].cwd == str(session_cwd)
        header = json.loads(
            session.state[DSH_SESSION_STATE_KEY]["jsonl"].splitlines()[0])
        assert header["cwd"] == str(session_cwd)

    asyncio.run(scenario())


def test_custom_dsh_input_can_replace_jsonl(tmp_path):
    async def scenario():
        async def custom_dsh_input(ctx, run_input):
            header = {
                "type": "session",
                "version": 4,
                "id": run_input.session_id,
                "createdAt": 1,
                "isSeeded": False,
                "delegationDepth": 0,
            }
            seed = {"type": "session/end-seed", "seq": 0, "time": 1, "data": {}}
            run_input.jsonl = json.dumps(header) + "\n" + json.dumps(seed) + "\n"
            return run_input

        agent = DSHAgent(
            name="dsh_agent",
            dsh_conf=_dsh_conf(tmp_path),
            custom_dsh_input=custom_dsh_input,
        )
        runtime = _FakeRuntime()
        agent._runtime = runtime
        await agent.init()

        _, session = await _run_single_turn(agent, _text("question"))
        request = runtime.requests[0]
        assert request.version == 4
        assert json.loads(request.jsonl.splitlines()[0])["createdAt"] == 1
        assert DSH_SESSION_STATE_KEY in session.state
        assert '"text":"first"' in session.state[DSH_SESSION_STATE_KEY]["jsonl"]

    asyncio.run(scenario())


def test_custom_dsh_input_must_return_run_input(tmp_path):
    async def scenario():
        async def custom_dsh_input(ctx, run_input):
            run_input.meta["from_callback"] = True
            return None

        agent = DSHAgent(
            name="dsh_agent",
            dsh_conf=_dsh_conf(tmp_path),
            custom_dsh_input=custom_dsh_input,
        )
        run_input = DSHRunInput(
            session_id="s1",
            content_blocks=[{"type": "text", "text": "hi"}],
        )

        with pytest.raises(TypeError, match="must return a DSHRunInput"):
            await agent._apply_custom_dsh_input(object(), run_input)

    asyncio.run(scenario())


def test_custom_dsh_input_can_append_durable_block(tmp_path):
    async def scenario():
        async def custom_dsh_input(ctx, run_input):
            run_input.content_blocks.append({
                "type": "image",
                "attachment": {
                    "attachmentId": "att-1",
                    "mediaType": "image/png",
                    "bytes": 3,
                    "width": 1,
                    "height": 1,
                },
            })
            return run_input

        agent = DSHAgent(
            name="dsh_agent",
            dsh_conf=_dsh_conf(tmp_path),
            custom_dsh_input=custom_dsh_input,
        )
        runtime = _FakeRuntime()
        agent._runtime = runtime
        await agent.init()

        await _run_single_turn(agent, _text("question"))
        assert runtime.requests[0].content_blocks[1]["type"] == "image"
        assert runtime.requests[0].content_blocks[1]["attachment"]["attachmentId"] == "att-1"

    asyncio.run(scenario())
