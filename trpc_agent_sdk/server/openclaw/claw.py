# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# This file is part of tRPC-Agent-Python and is licensed under Apache-2.0.
#
# Portions of this file are derived from HKUDS/nanobot (MIT License):
# https://github.com/HKUDS/nanobot.git
#
# Copyright (c) 2025 nanobot contributors
#
# See the project LICENSE / third-party attribution notices for details.
#
"""OpenClaw gateway runner.

This module aligns OpenClaw runtime behavior with nanobot's AgentLoop gateway:

- Consume inbound messages from MessageBus (third-party channels)
- Process each message asynchronously via Runner
- Support /stop to cancel in-flight tasks per session
- Route outbound responses back to channels
- Integrate CronService + ClawHeartbeatService callbacks
- Keep long-term / short-term memory wiring (ClawMemoryService / ClawSessionService)

When no third-party channel is enabled, this runner falls back to a local CLI
loop that still uses the same bus-based processing pipeline.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Optional

from dotenv import load_dotenv
from nanobot.bus.events import InboundMessage
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.manager import ChannelManager
from nanobot.cron.types import CronJob
from nanobot.utils.helpers import ensure_dir
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import set_summarizer_events_count_threshold
from trpc_agent_sdk.storage import RedisStorage
from trpc_agent_sdk.storage import SqlStorage
from trpc_agent_sdk.types import Content

from ._logger import default_logger
from ._logger import init_claw_logger
from ._utils import build_user_parts
from ._utils import is_channel_supports_stream_progress
from ._utils import merge_assistant_text
from ._utils import merge_raw_events
from ._utils import parse_origin
from .agent import create_agent
from .agent import create_model
from .channels import TrpcClawCommandHandler
from .channels import TrpcClawCommandHandlerParams
from .channels import repair_channels
from .config import ClawConfig
from .config import DEFAULT_USER_ID
from .config import FileStorageConfig
from .config import load_config
from .metrics import setup_metrics
from .service import ClawHeartbeatService
from .service import CronService
from .session_memory import ClawMemoryService
from .session_memory import ClawSessionService
from .session_memory import ClawSummarizerSessionManager
from .skill import ClawSkillLoader
from .storage import AioFileStorage
from .storage import RAW_EVENTS_KEY
from .storage import StorageManager
from .storage import set_agent_context
from .tools.cron import CRON_CHANNEL_KEY
from .tools.cron import CRON_CHAT_ID_KEY
from .tools.cron import CRON_IN_CONTEXT_KEY
from .tools.message import MESSAGE_CALLBACK_KEY
from .tools.message import MESSAGE_CHANNEL_KEY
from .tools.message import MESSAGE_CHAT_ID_KEY
from .tools.message import MESSAGE_ID_KEY
from .tools.message import MESSAGE_SENT_IN_TURN_KEY
from .tools.spawn_task import SPAWN_TASK_CHANNEL_KEY
from .tools.spawn_task import SPAWN_TASK_CHAT_ID_KEY
from .tools.spawn_task import SPAWN_TASK_SESSION_KEY
from .tools.spawn_task import SPAWN_TASK_SUBMIT_CALLBACK_KEY
from .tools.spawn_task import SPAWN_TASK_USER_ID_KEY

load_dotenv()

default_logger()

_HEARTBEAT_USER_ID = "_system"
_HEARTBEAT_SESSION_ID = "heartbeat"
_CRON_USER_ID = "_cron"


class ClawApplication:
    """Claw application runtime."""

    def __init__(self, workspace: Optional[Path] = None, config_path: Optional[Path] = None) -> None:
        self.config: ClawConfig = load_config(config_path)
        if workspace is not None:
            self.config.agent.workspace = str(workspace.expanduser().resolve())
        self.workspace = self.config.workspace
        init_claw_logger(self.config.logger)
        setup_metrics(self.config)
        self.model = create_model(config=self.config)

        # Message bus and channel manager
        self.bus = MessageBus()
        self.channels = ChannelManager(self.config, self.bus)

        repair_channels(self.channels)
        # Memory storage shared by short-term and long-term services
        if self.config.storage.type == "sql":
            if not self.config.storage.sql:
                raise ValueError("Sql storage configuration is required")
            config = self.config.storage.sql
            self._storage = SqlStorage(is_async=config.is_async, db_url=config.url, **config.kwargs)
        elif self.config.storage.type == "redis":
            if not self.config.storage.redis:
                raise ValueError("Redis storage configuration is required")
            config = self.config.storage.redis
            self._storage = RedisStorage(is_async=config.is_async, db_url=config.url, **config.kwargs)
        else:
            memory_dir = ensure_dir(self.workspace / "memory")
            config = self.config.storage.file or FileStorageConfig(base_dir=str(memory_dir))
            self._storage = AioFileStorage(config=config)
        self._storage_manager = StorageManager(storage=self._storage)
        if not self.config.agent.memory_window:
            self.config.agent.memory_window = 30
        memory_window = max(30, int(self.config.agent.memory_window))

        self._summarizer_manager = ClawSummarizerSessionManager(
            model=self.model,
            storage_manager=self._storage_manager,
            auto_summarize=True,
            # Align with nanobot trigger: consolidate when unconsolidated
            # conversation reaches memory window size.
            check_summarizer_functions=[set_summarizer_events_count_threshold(memory_window)],
            # Keep half-window recent events (nanobot uses memory_window // 2).
            keep_recent_count=max(1, memory_window // 2),
        )
        self.session_service = ClawSessionService(
            config=self.config,
            summarizer_manager=self._summarizer_manager,
        )
        memory_service_config = self.config.memory.memory_service_config
        memory_service_config.enabled = True
        self.memory_service = ClawMemoryService(storage_manager=self._storage_manager,
                                                memory_service_config=memory_service_config)

        # Scheduled services
        cron_dir = ensure_dir(self.workspace / "cron")
        self.cron_service = CronService(store_path=cron_dir / "jobs.json")

        # Agent + runner
        self.agent = create_agent(
            config=self.config,
            model=self.model,
            cron_service=self.cron_service,
        )
        self.runner = Runner(
            app_name=self.config.runtime.app_name,
            agent=self.agent,
            session_service=self.session_service,
            memory_service=self.memory_service,
        )
        worker_agent = self.agent.sub_agents[0] if self.agent.sub_agents else self.agent
        self.worker_runner = Runner(
            app_name=f"{self.config.runtime.app_name}_worker",
            agent=worker_agent,
            session_service=self.session_service,
            memory_service=self.memory_service,
        )

        hb_cfg = self.config.gateway.heartbeat
        self.heartbeat = ClawHeartbeatService(
            workspace=self.workspace,
            provider=self.model,
            model=self.model.name,
            on_execute=self._on_heartbeat_execute,
            on_notify=self._on_heartbeat_notify,
            interval_s=hb_cfg.interval_s,
            enabled=hb_cfg.enabled,
        )

        self.cron_service.on_job = self._on_cron_job

        self._running = False
        self._inbound_loop_task: asyncio.Task | None = None
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        self._background_tasks: dict[str, list[asyncio.Task]] = {}
        self._processing_lock = asyncio.Lock()
        self._last_external_target: tuple[str, str] = ("cli", "direct")
        cmd_params = TrpcClawCommandHandlerParams(config=self.config,
                                                  bus=self.bus,
                                                  session_service=self.session_service,
                                                  active_tasks=self._active_tasks,
                                                  background_tasks=self._background_tasks)
        self.command_handler = TrpcClawCommandHandler(params=cmd_params)

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------

    async def _submit_background_task(
        self,
        *,
        task: str,
        label: str | None,
        origin_channel: str,
        origin_chat_id: str,
        session_key: str,
        user_id: str,
    ) -> str:
        """Submit a background task without blocking current conversation."""
        task_id = uuid.uuid4().hex[:8]
        task_label = (label or task).strip() or "background task"
        if len(task_label) > 48:
            task_label = task_label[:45] + "..."

        started = (f"Background task '{task_label}' started (id: {task_id}). "
                   "I will notify you when it completes.")
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=origin_channel,
                chat_id=origin_chat_id,
                content=started,
                metadata={
                    "_progress": True,
                    "_background_task": True,
                    "_task_id": task_id
                },
            ))

        params = {
            "session_key": session_key,
            "task_id": task_id,
            "task": task,
            "user_id": user_id,
            "origin_channel": origin_channel,
            "origin_chat_id": origin_chat_id,
            "task_label": task_label
        }
        task_obj = asyncio.create_task(self._background_task_runner(**params))
        self._background_tasks.setdefault(session_key, []).append(task_obj)

        task_obj.add_done_callback(lambda t, key=session_key: self._cleanup_background_tasks(t, key))
        return started

    async def _background_task_runner(self, session_key: str, task_id: str, task: str, user_id: str,
                                      origin_channel: str, origin_chat_id: str, task_label: str) -> None:
        status = "ok"
        result = ""
        bg_session_id = f"{session_key}:bg:{task_id}"
        try:
            result = await self._run_turn(
                user_id=f"{user_id}_bg",
                session_id=bg_session_id,
                query=task,
                channel=origin_channel,
                chat_id=origin_chat_id,
                stream_progress=False,
                passthrough_metadata={
                    "_background_task": True,
                    "_task_id": task_id
                },
                use_worker_agent=True,
            )
            if not result:
                result = "Task completed."
        except asyncio.CancelledError:
            status = "cancelled"
            result = "Task was cancelled."
            raise
        except Exception as ex:  # pylint: disable=broad-except
            status = "error"
            result = f"Error: {ex}"
            logger.error("Background task %s failed", task_id)
        finally:
            status_text = ("completed successfully"
                           if status == "ok" else "was cancelled" if status == "cancelled" else "failed")
            summary_prompt = (f"[Background task '{task_label}' {status_text}]\n\n"
                              f"Task: {task}\n\n"
                              f"Result:\n{result}\n\n"
                              "Summarize this naturally for the user. Keep it brief (1-2 sentences). "
                              "Do not mention internal IDs or implementation details.")
            await self.bus.publish_inbound(
                InboundMessage(
                    channel="system",
                    sender_id="background_task",
                    chat_id=f"{origin_channel}:{origin_chat_id}",
                    content=summary_prompt,
                    metadata={
                        "_background_task": True,
                        "_task_id": task_id,
                        "_origin_session_key": session_key,
                    },
                ))

    def _cleanup_background_tasks(self, task: asyncio.Task, key: str) -> None:
        tasks = self._background_tasks.get(key, [])
        if task in tasks:
            tasks.remove(task)
        if not tasks and key in self._background_tasks:
            del self._background_tasks[key]

    async def _run_turn(
        self,
        *,
        user_id: str,
        session_id: str,
        query: str,
        media: Optional[list[str]] = None,
        channel: str,
        chat_id: str,
        message_id: str | None = None,
        stream_progress: bool = True,
        progress_callback: Optional[Callable[[str], Awaitable[None] | None]] = None,
        in_cron_context: bool = False,
        passthrough_metadata: Optional[dict[str, Any]] = None,
        use_worker_agent: bool = False,
    ) -> str:
        """Execute one agent turn and return the final accumulated text."""
        metadata = dict(passthrough_metadata or {})
        metadata.update({
            # Message tool delivery
            MESSAGE_CALLBACK_KEY: self.bus.publish_outbound,
            MESSAGE_CHANNEL_KEY: channel,
            MESSAGE_CHAT_ID_KEY: chat_id,
            MESSAGE_ID_KEY: message_id,
            # Cron tool delivery + guard
            CRON_CHANNEL_KEY: channel,
            CRON_CHAT_ID_KEY: chat_id,
            CRON_IN_CONTEXT_KEY: in_cron_context,
            # Background task dispatch
            SPAWN_TASK_SUBMIT_CALLBACK_KEY: self._submit_background_task,
            SPAWN_TASK_CHANNEL_KEY: channel,
            SPAWN_TASK_CHAT_ID_KEY: chat_id,
            SPAWN_TASK_SESSION_KEY: session_id,
            SPAWN_TASK_USER_ID_KEY: user_id,
            # Turn flag
            MESSAGE_SENT_IN_TURN_KEY: False,
        })

        agent_context = new_agent_context(metadata=metadata)
        content = Content(parts=build_user_parts(query=query, media=media))

        final_text = ""

        runner = self.worker_runner if use_worker_agent else self.runner
        async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=content,
                agent_context=agent_context,
        ):
            if not event.content or not event.content.parts:
                continue

            # Partial events are streaming deltas; they should never be mixed into
            # final text aggregation. Optionally forward them as progress updates.
            if event.partial:
                if stream_progress:
                    chunk = "".join(part.text for part in event.content.parts if part.text and not part.thought)
                    if chunk:
                        if progress_callback is not None:
                            result = progress_callback(chunk)
                            if result is not None:
                                await result
                        else:
                            progress_meta = dict(passthrough_metadata or {})
                            progress_meta["_progress"] = True
                            await self.bus.publish_outbound(
                                OutboundMessage(
                                    channel=channel,
                                    chat_id=chat_id,
                                    content=chunk,
                                    metadata=progress_meta,
                                ))
                continue

            has_function_call = False

            # Emit tool hints during non-partial events when function calls appear
            if self.config.channels.send_tool_hints:
                for part in event.content.parts:
                    if part.function_call:
                        has_function_call = True
                        hint_meta = dict(passthrough_metadata or {})
                        hint_meta["_progress"] = True
                        hint_meta["_tool_hint"] = True
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=channel,
                                chat_id=chat_id,
                                content=f"{part.function_call.name}({part.function_call.args})",
                                metadata=hint_meta,
                            ))
            else:
                has_function_call = any(part.function_call for part in event.content.parts)

            # Skip text from events that contain tool calls; these are often
            # pre-tool drafts and can cause duplicated final answers.
            if has_function_call:
                continue

            event_text = "".join(part.text for part in event.content.parts if part.text and not part.thought)
            if event_text:
                final_text = merge_assistant_text(final_text, event_text)

        # If the message tool already sent the user-facing reply, do not duplicate.
        await self._persist_session_after_turn(
            app_name=self.config.runtime.app_name,
            user_id=user_id,
            session_id=session_id,
            agent_context=agent_context,
        )
        if agent_context.get_metadata(MESSAGE_SENT_IN_TURN_KEY, False):
            return ""

        return final_text

    async def _persist_session_after_turn(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        agent_context: AgentContext,
    ) -> None:
        """Persist session snapshot after each completed turn.

        This avoids losing unsummarized raw events on unexpected process exit.
        """
        try:
            session = await self.session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                agent_context=agent_context,
            )
            if session is None:
                return
            # Keep full raw archive durable even when summarization does not run.
            existing_raw = agent_context.get_metadata(RAW_EVENTS_KEY, []) or []
            merged_raw = merge_raw_events(existing_raw, list(session.events))
            agent_context.with_metadata(RAW_EVENTS_KEY, merged_raw)
            set_agent_context(agent_context)
            await self.session_service.update_session(session)
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning(
                "Failed to persist session after turn app=%s user=%s session=%s: %s",
                app_name,
                user_id,
                session_id,
                ex,
            )

    async def _process_message(
        self,
        msg: InboundMessage,
        *,
        stream_progress_override: Optional[bool] = None,
        progress_callback: Optional[Callable[[str], Awaitable[None] | None]] = None,
    ) -> Optional[OutboundMessage]:
        """Process one inbound message and return an outbound response."""
        self._refresh_skill_repository()
        channel, chat_id = parse_origin(msg)

        if channel not in {"cli", "system"}:
            self._last_external_target = (channel, chat_id)

        user_id = msg.sender_id or f"{channel}_{DEFAULT_USER_ID}"
        session_id = msg.session_key
        query = msg.content

        # Progress strategy by channel:
        # - CLI: use dedicated callback streaming in run_cli_fallback.
        # - Telegram/WeCom: disable bus-level progress chunks to avoid message spam.
        #   These channels already provide their own better UX reply behavior.
        is_stream_progress_enabled = is_channel_supports_stream_progress(channel)
        stream_progress = (stream_progress_override if stream_progress_override else
                           (self.config.channels.send_progress and is_stream_progress_enabled))
        response_text = await self._run_turn(
            user_id=user_id,
            session_id=session_id,
            query=query,
            media=msg.media if msg.media else None,
            channel=channel,
            chat_id=chat_id,
            message_id=msg.metadata.get("message_id"),
            stream_progress=stream_progress,
            progress_callback=progress_callback,
            passthrough_metadata=msg.metadata,
        )

        if not response_text:
            return None
        return OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=response_text,
            metadata=msg.metadata,
        )

    def _refresh_skill_repository(self) -> None:
        """Refresh skill repository index before each turn."""
        repository = getattr(self.agent, "skill_repository", None)
        if not isinstance(repository, ClawSkillLoader):
            return
        repository.refresh()

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process inbound message under lock and publish output."""
        async with self._processing_lock:
            try:
                response = await self._process_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel="cli",
                            chat_id=msg.chat_id,
                            content="",
                            metadata=msg.metadata or {},
                        ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session %s", msg.session_key)
                raise
            except Exception as ex:
                logger.error("Error processing message for session %s: %s", msg.session_key, ex)
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="Sorry, I encountered an error.",
                    ))

    async def _inbound_loop(self) -> None:
        """Consume bus inbound queue and dispatch messages asynchronously."""
        logger.info("Inbound loop started")
        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            is_handled = await self.command_handler.handle(msg)
            if is_handled:
                continue

            task = asyncio.create_task(self._dispatch(msg))
            key = msg.session_key
            self._active_tasks.setdefault(key, []).append(task)

            def _cleanup(t: asyncio.Task, session_key: str = key) -> None:
                tasks = self._active_tasks.get(session_key, [])
                if t in tasks:
                    tasks.remove(t)
                if not tasks and session_key in self._active_tasks:
                    del self._active_tasks[session_key]

            task.add_done_callback(_cleanup)

    # ------------------------------------------------------------------
    # Heartbeat + Cron callbacks
    # ------------------------------------------------------------------
    async def _on_heartbeat_execute(self, tasks: str) -> str:
        """Execute heartbeat tasks through the full agent loop."""
        channel, chat_id = self._last_external_target
        logger.info("Heartbeat executing tasks on %s:%s", channel, chat_id)
        return await self._run_turn(
            user_id=_HEARTBEAT_USER_ID,
            session_id=_HEARTBEAT_SESSION_ID,
            query=tasks,
            channel=channel,
            chat_id=chat_id,
            stream_progress=False,
            passthrough_metadata={"_heartbeat": True},
        )

    async def _on_heartbeat_notify(self, response: str) -> None:
        """Deliver heartbeat response to user channel (skip pure CLI fallback)."""
        channel, chat_id = self._last_external_target
        if channel == "cli":
            logger.info("Heartbeat response (cli): %s", response)
            return
        await self.bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=response))

    async def _on_cron_job(self, job: CronJob) -> str | None:
        """Execute cron job through the same runner pipeline."""
        if not job.payload.message:
            logger.warning("Cron job {!r} has empty message", job.name)
            return None

        channel = job.payload.channel or "cli"
        chat_id = job.payload.to or "direct"
        reminder = ("[Scheduled Task] Timer finished.\n\n"
                    f"Task '{job.name}' has been triggered.\n"
                    f"Scheduled instruction: {job.payload.message}")

        result = await self._run_turn(
            user_id=_CRON_USER_ID,
            session_id=f"cron:{job.id}",
            query=reminder,
            channel=channel,
            chat_id=chat_id,
            stream_progress=False,
            in_cron_context=True,
            passthrough_metadata={"_cron": True},
        )

        if job.payload.deliver and job.payload.to and result:
            await self.bus.publish_outbound(OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=result,
            ))

        return result

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start gateway runtime: cron, heartbeat, channel manager, inbound loop."""
        if self._running:
            return
        self._running = True

        await self.cron_service.start()
        await self.heartbeat.start()
        self._inbound_loop_task = asyncio.create_task(self._inbound_loop())

        enabled = self.channels.enabled_channels
        if enabled:
            logger.info("Channels enabled: %s", ", ".join(enabled))
        else:
            logger.warning("No third-party channels enabled; using CLI fallback mode")

        cron_status = self.cron_service.status()
        if cron_status.get("jobs", 0) > 0:
            logger.info("Cron jobs loaded: %s", cron_status["jobs"])

        logger.info("Claw gateway started  workspace= %s", self.workspace)

    async def stop(self) -> None:
        """Stop gateway runtime gracefully."""
        if not self._running:
            return
        self._running = False

        if self._inbound_loop_task:
            self._inbound_loop_task.cancel()
            await asyncio.gather(self._inbound_loop_task, return_exceptions=True)
            self._inbound_loop_task = None

        # cancel active message tasks
        all_tasks = [t for tasks in self._active_tasks.values() for t in tasks]
        for t in all_tasks:
            if not t.done():
                t.cancel()
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)
        self._active_tasks.clear()

        # cancel background tasks
        all_bg_tasks = [t for tasks in self._background_tasks.values() for t in tasks]
        for t in all_bg_tasks:
            if not t.done():
                t.cancel()
        if all_bg_tasks:
            await asyncio.gather(*all_bg_tasks, return_exceptions=True)
        self._background_tasks.clear()

        self.heartbeat.stop()
        self.cron_service.stop()
        await self.channels.stop_all()
        logger.info("Claw gateway stopped")

    # ------------------------------------------------------------------
    # Entry runners
    # ------------------------------------------------------------------

    async def run_gateway(self) -> None:
        """Run with third-party channels (or none, if disabled in config)."""
        await self.start()
        try:
            await asyncio.gather(
                self.channels.start_all(),
                self._wait_forever(),
            )
        finally:
            await self.stop()

    async def run_cli_fallback(self) -> None:
        """CLI fallback that still goes through MessageBus + inbound loop."""
        await self.start()
        loop = asyncio.get_event_loop()

        print(f"Claw is ready. workspace={self.workspace}")
        print("Commands: /new, /stop, /help, /quit")

        try:
            while True:
                try:
                    raw = await loop.run_in_executor(None, lambda: input("You: "))
                except (EOFError, KeyboardInterrupt):
                    print()
                    break

                query = raw.strip()
                if not query:
                    continue
                if query in {"/quit", "/exit", "quit", "exit"}:
                    break

                # Process synchronously in CLI mode so prompt/output ordering is stable:
                # one "You:" input -> one "Assistant:" output.
                msg = InboundMessage(
                    channel="cli",
                    sender_id=DEFAULT_USER_ID,
                    chat_id="direct",
                    content=query,
                )
                streamed = False
                printed_header = False

                async def _cli_progress(chunk: str) -> None:
                    nonlocal streamed, printed_header
                    if not printed_header:
                        print("\nAssistant: ", end="", flush=True)
                        printed_header = True
                    streamed = True
                    print(chunk, end="", flush=True)

                async with self._processing_lock:
                    is_handled = await self.command_handler.handle(msg)
                    if is_handled:
                        while True:
                            try:
                                outbound = self.bus.outbound.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                            if outbound.channel == "cli" and outbound.content:
                                print(f"\nAssistant: {outbound.content}")
                        continue
                    response = await self._process_message(
                        msg,
                        stream_progress_override=True,
                        progress_callback=_cli_progress,
                    )

                if streamed:
                    print()
                elif response is not None and response.content:
                    print(f"\nAssistant: {response.content}")
        finally:
            await self.stop()

    async def _wait_forever(self) -> None:
        while self._running:
            await asyncio.sleep(1)
