# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""trpc-claw configuration loader.

This module provides a config object that:
1. loads from YAML/JSON file
2. applies environment-variable overrides
3. exposes nanobot-compatible config sections used by channel manager/runtime
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from typing import Optional

import yaml
from nanobot.config.loader import set_config_path
from nanobot.config.schema import AgentDefaults
from nanobot.config.schema import Config as NanobotConfig
from nanobot.utils.helpers import ensure_dir
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.code_executors import CodeBlockDelimiter
from trpc_agent_sdk.server.langfuse.tracing.opentelemetry import LangfuseConfig

from ._constants import AGENT_FILE_NAME
from ._constants import DEFAULT_APP_NAME
from ._constants import DEFAULT_CONFIG_PATH
from ._constants import DEFAULT_LEGACY_SESSIONS_DIR
from ._constants import DEFAULT_TRPC_AGENT_CLAW_DIR
from ._constants import DEFAULT_USER_ID
from ._constants import DEFAULT_WORKSPACE_PATH
from ._constants import HISTORY_FILE_NAME
from ._constants import MEMORY_FILE_NAME
from ._constants import SOUL_FILE_NAME
from ._constants import TOOL_FILE_NAME
from ._constants import TRPC_AGENT_CLAW_CONFIG
from ._constants import USER_FILE_NAME


class LocalCodeExecutorConfig(BaseModel):
    """trpc-claw local code executor config."""
    workspace: str = ""
    read_only_staged_skill: bool = False
    auto_inputs: bool = True
    inputs_host_base: str = ""


class ContainerCodeExecutorConfig(BaseModel):
    """trpc-claw container code executor config."""
    base_url: Optional[str] = None
    """The base url of the user hosted Docker client."""
    image: str = "python:3-slim"
    """The tag of the predefined image or custom image to run on the container.
    Either docker_path or image must be set.
    """
    docker_path: Optional[str] = None
    """The path to the directory containing the Dockerfile.
    If set, build the image from the dockerfile path instead of using the
    predefined image. Either docker_path or image must be set.
    """
    auto_inputs: bool = False
    """Whether to auto-map inputs."""
    inputs_host_base: str = ""


class SkillConfig(BaseModel):
    """Per-skill runtime config."""
    enabled: Optional[bool] = None
    env: dict[str, str] = Field(default_factory=dict)


class SkillRootConfig(BaseModel):
    """trpc-claw skill root config."""
    sandbox_type: str = "local"
    skill_roots: list[str] = Field(default_factory=list)
    builtin_skill_roots: list[str] = Field(default_factory=list)
    config_keys: list[str] = Field(default_factory=list)
    allow_bundled: list[str] = Field(default_factory=list)
    skill_configs: dict[str, SkillConfig] = Field(default_factory=dict)
    local_config: LocalCodeExecutorConfig = Field(default_factory=LocalCodeExecutorConfig)
    container_config: ContainerCodeExecutorConfig = Field(default_factory=ContainerCodeExecutorConfig)
    run_tool_kwargs: dict[str, Any] = Field(default_factory=dict)
    debug: bool = False
    """The debug mode."""
    bundled_root: str = ""
    """The bundled root."""


class RuntimeConfig(BaseModel):
    """trpc-claw runtime-only config (not part of nanobot schema)."""

    app_name: str = DEFAULT_APP_NAME
    user_id: str = DEFAULT_USER_ID
    legacy_sessions_dir: str = str(DEFAULT_LEGACY_SESSIONS_DIR)


class AgentConfig(AgentDefaults):
    """trpc-claw agent config."""
    instruction: str = ""
    system_prompt: str = ""
    api_key: str = ""
    api_base: str = ""
    extra_headers: dict[str, str] = Field(default_factory=dict)
    memory_window: int = Field(default=30, ge=30, le=10000)


class MemoryConfig(BaseModel):
    """trpc-claw memory config."""
    memory_service_config: MemoryServiceConfig = Field(default_factory=MemoryServiceConfig)


class FileStorageConfig(BaseModel):
    """trpc-claw file storage config."""
    base_dir: str = ""
    max_key_length: int = 255


class SqlStorageConfig(BaseModel):
    """trpc-claw sql storage config."""
    url: str = ""
    is_async: bool = False
    kwargs: dict[str, Any] = Field(default_factory=dict)


class RedisStorageConfig(BaseModel):
    """trpc-claw redis storage config."""
    url: str = ""
    is_async: bool = False
    password: str = ""
    db: int = 0
    kwargs: dict[str, Any] = Field(default_factory=dict)


class StorageConfig(BaseModel):
    """trpc-claw storage config."""
    model_config = ConfigDict(extra="forbid", )
    """The pydantic model config."""
    type: str = "file"
    """The storage type."""
    file: Optional[FileStorageConfig] = None
    redis: Optional[RedisStorageConfig] = None
    sql: Optional[SqlStorageConfig] = None


class LoggerConfig(BaseModel):
    """trpc-claw logger config."""
    name: str = "trpc-claw"
    """The logger name."""
    log_file: str = "trpc_claw.log"
    """The log file."""
    log_level: str = "INFO"
    """The log level."""
    log_format: str = "[%(asctime)s][%(levelname)s][%(name)s][%(pathname)s:%(lineno)d][%(process)d] %(message)s"


class MetricsConfig(BaseModel):
    """trpc-claw metrics config."""
    type: str = "langfuse"
    """The metrics type."""
    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)


class ClawConfig(NanobotConfig):
    """Root trpc-claw config.

    Fields are intentionally aligned with nanobot's top-level config sections
    so existing channel/cron/heartbeat code can reuse the same access pattern.
    """

    agent: AgentConfig = Field(default_factory=AgentConfig)
    skills: SkillRootConfig = Field(default_factory=SkillRootConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    logger: LoggerConfig = Field(default_factory=LoggerConfig)
    personal: list[str] = Field(default_factory=list)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)

    @property
    def workspace(self) -> Path:
        """Resolved workspace path.

        Returns:
            Path: The resolved workspace path.
        """
        return Path(self.agent.workspace).expanduser().resolve()

    @property
    def model_name(self) -> str:
        """Resolved model name.

        Returns:
            str: The resolved model name.
        """
        return self.agent.model

    @property
    def model_api_key(self) -> str:
        """Resolved model API key.

        Returns:
            str: The resolved model API key.
        """
        return self.agent.api_key

    @property
    def model_base_url(self) -> str:
        """Resolved model base URL.

        Returns:
            str: The resolved model base URL.
        """
        return self.agent.api_base

    @property
    def model_extra_headers(self) -> dict[str, str]:
        """Resolved model extra headers.

        Returns:
            dict[str, str]: The resolved model extra headers.
        """
        return self.agent.extra_headers

    @property
    def skill_roots(self) -> str:
        """Resolved skill roots.

        Returns:
            list[str]: The resolved skill roots.
        """
        return self.skills.skill_roots


def _read_config_file(path: Path) -> dict[str, Any]:
    """Read YAML/JSON config file into a dict.

    Args:
        path: The path to the config file.

    Returns:
        dict[str, Any]: The config file as a dictionary.
    """
    if not path.exists():
        return {}

    if path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise ValueError("PyYAML is required to load YAML config files")
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Invalid YAML config format: {path}")
        return data

    with open(path, encoding="utf-8") as f:
        data = json.load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid JSON config format: {path}")
    return data


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand environment variables for config values.

    Args:
        value: The value to expand.

    Returns:
        Any: The expanded value.
    """
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    return value


def create_inner_dirs_and_files(config: ClawConfig):
    """Create inner dirs for the config."""
    ensure_dir(config.workspace)
    # sessions
    ensure_dir(config.workspace / "sessions")
    soul_file = config.workspace / SOUL_FILE_NAME
    soul_file.touch(exist_ok=True)
    user_file = config.workspace / USER_FILE_NAME
    user_file.touch(exist_ok=True)
    tool_file = config.workspace / TOOL_FILE_NAME
    tool_file.touch(exist_ok=True)
    agent_file = config.workspace / AGENT_FILE_NAME
    agent_file.touch(exist_ok=True)
    # memory
    ensure_dir(config.workspace / "memory")
    history_file = config.workspace / "memory" / HISTORY_FILE_NAME
    history_file.touch(exist_ok=True)
    memory_file = config.workspace / "memory" / MEMORY_FILE_NAME
    memory_file.touch(exist_ok=True)
    # skills
    ensure_dir(config.workspace / "skills")
    # skills workspace
    ensure_dir(Path(config.skills.local_config.workspace))


def load_config(config_path: Optional[Path] = None) -> ClawConfig:
    """Load config from YAML/JSON file then apply env overrides.

    Search order:
    1) explicit config_path
    2) ``$CLAW_CONFIG``
    3) ``DEFAULT_CONFIG_PATH`` (json)
    4) sibling yaml: ``config.yaml`` / ``config.yml`` (if present)
    """
    if not config_path:
        config_path = os.getenv(TRPC_AGENT_CLAW_CONFIG, "").strip()
        if not config_path:
            if not DEFAULT_TRPC_AGENT_CLAW_DIR.exists():
                DEFAULT_TRPC_AGENT_CLAW_DIR.mkdir(parents=True, exist_ok=True)
            config_path = str(DEFAULT_CONFIG_PATH)

    path = Path(config_path)
    set_config_path(config_path)

    raw = _read_config_file(path)
    if raw:
        raw = _expand_env_vars(raw)
        # Backward compatibility: accept legacy top-level "agents" key.
        if "agent" not in raw and "agents" in raw and isinstance(raw["agents"], dict):
            raw["agent"] = raw.pop("agents")
    need_default_workspace = False
    if "agent" not in raw or "workspace" not in raw["agent"]:
        need_default_workspace = True
    cfg = ClawConfig.model_validate(raw) if raw else ClawConfig()
    if need_default_workspace:
        cfg.agent.workspace = str(DEFAULT_WORKSPACE_PATH)
    if not cfg.agent.api_key:
        cfg.agent.api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    if not cfg.agent.api_base:
        cfg.agent.api_base = os.getenv("TRPC_AGENT_BASE_URL", "")
    if not cfg.agent.model or "agent" not in raw:
        cfg.agent.model = os.getenv("TRPC_AGENT_MODEL_NAME", "")
    telegram = getattr(cfg.channels, "telegram", {})
    if telegram:
        if 'token' not in telegram or not telegram['token']:
            telegram['token'] = os.getenv("TELEGRAM_BOT_TOKEN", "")
    wecom = getattr(cfg.channels, "wecom", {})
    if wecom:
        if 'bot_id' not in wecom or not wecom['bot_id']:
            wecom['bot_id'] = os.getenv("WECOM_BOT_ID", "")
        if 'secret' not in wecom or not wecom['secret']:
            wecom['secret'] = os.getenv("WECOM_BOT_SECRET", "")
    if not cfg.skills.local_config.workspace:
        cfg.skills.local_config.workspace = f"{cfg.agent.workspace}/skills_ws"
    create_inner_dirs_and_files(cfg)
    return cfg
