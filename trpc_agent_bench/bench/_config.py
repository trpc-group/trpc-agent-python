"""User-facing benchmark configuration objects."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any


@dataclass
class ModelConf:
    """Behavior of the internal OpenAI-compatible mock model."""

    request_delay_ms: float = 0.0
    chunk_delay_ms: float = 0.0


@dataclass
class AgentBenchConf:
    """User configuration shared by every benchmark run."""

    app_name: str = "bench_app"
    user_id: str = "bench_user"
    agent_conf: Any = None
    model_conf: ModelConf = field(default_factory=ModelConf)
    base_url: str | None = field(default=None, repr=False)
