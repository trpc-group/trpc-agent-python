"""Configuration loading for the code review agent."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReviewConfig:
    """Configuration for a code review run."""
    # Input
    diff_file: Optional[str] = None
    repo_path: Optional[str] = None

    # Filter
    filter_enabled: bool = True
    denied_patterns: list[str] = field(default_factory=lambda: [
        r"rm\s+-rf\s+/",
        r"curl.*\|.*sh",
        r"eval\s+",
        r"__import__\s*\(\s*['\"]os['\"]",
    ])

    # Sandbox
    sandbox_timeout_seconds: int = 30
    sandbox_max_output_bytes: int = 1024 * 1024  # 1MB
    sandbox_env_allowlist: list[str] = field(default_factory=lambda: [
        "PATH", "HOME", "USER", "PYTHONPATH", "LANG"
    ])

    # Scanners
    enabled_scanners: list[str] = field(default_factory=lambda: [
        "security", "async_error", "resource_leak",
        "db_lifecycle", "missing_tests", "secret_info"
    ])
    min_confidence: float = 0.5

    # Storage
    db_path: str = "review_history.db"

    # Output
    output_dir: str = "."
    dry_run: bool = False
    verbose: bool = False


def load_config(**kwargs) -> ReviewConfig:
    """Load configuration with optional overrides."""
    cfg = ReviewConfig()
    for k, v in kwargs.items():
        if v is not None and hasattr(cfg, k):
            setattr(cfg, k, v)
    return cfg
