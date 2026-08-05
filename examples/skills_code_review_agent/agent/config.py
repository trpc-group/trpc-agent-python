"""Model configuration for the review agent."""

import os
import math
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ModelConfig:
    """Configuration required by the OpenAI-compatible model client."""

    api_key: str
    base_url: str
    model_name: str

    @classmethod
    def from_env(cls) -> "ModelConfig":
        """Load and validate model settings from environment variables."""
        values = {
            "api_key": os.getenv("TRPC_AGENT_API_KEY", "").strip(),
            "base_url": os.getenv("TRPC_AGENT_BASE_URL", "").strip(),
            "model_name": os.getenv("TRPC_AGENT_MODEL_NAME", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            env_names = {
                "api_key": "TRPC_AGENT_API_KEY",
                "base_url": "TRPC_AGENT_BASE_URL",
                "model_name": "TRPC_AGENT_MODEL_NAME",
            }
            required = ", ".join(env_names[name] for name in missing)
            raise ValueError(f"Missing required environment variables: {required}")
        parsed_url = urlsplit(values["base_url"])
        loopback_hosts = {"localhost", "127.0.0.1", "::1"}
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
            or any(character.isspace() for character in values["base_url"])
        ):
            raise ValueError(
                "TRPC_AGENT_BASE_URL must be an HTTP(S) URL without credentials, "
                "query parameters, or fragments"
            )
        if parsed_url.scheme != "https" and parsed_url.hostname not in loopback_hosts:
            raise ValueError(
                "TRPC_AGENT_BASE_URL must use HTTPS unless it targets a loopback host"
            )
        allowed_hosts = {
            host.strip().lower()
            for host in os.getenv("TRPC_AGENT_ALLOWED_MODEL_HOSTS", "").split(",")
            if host.strip()
        }
        if allowed_hosts and parsed_url.hostname.lower() not in allowed_hosts:
            raise ValueError(
                "TRPC_AGENT_BASE_URL host is not in TRPC_AGENT_ALLOWED_MODEL_HOSTS"
            )
        return cls(**values)


@dataclass(frozen=True)
class ReviewLimits:
    """Whole-review budgets applied in addition to per-command limits."""

    timeout_seconds: float = 110.0
    max_tool_calls: int = 30

    @classmethod
    def from_env(cls) -> "ReviewLimits":
        timeout_seconds = float(os.getenv("CODE_REVIEW_TOTAL_TIMEOUT_SECONDS", "110"))
        max_tool_calls = int(os.getenv("CODE_REVIEW_MAX_TOOL_CALLS", "30"))
        if (
            not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 120
        ):
            raise ValueError(
                "CODE_REVIEW_TOTAL_TIMEOUT_SECONDS must be between 0 and 120"
            )
        if not 0 < max_tool_calls <= 30:
            raise ValueError("CODE_REVIEW_MAX_TOOL_CALLS must be between 1 and 30")
        return cls(
            timeout_seconds=timeout_seconds,
            max_tool_calls=max_tool_calls,
        )
