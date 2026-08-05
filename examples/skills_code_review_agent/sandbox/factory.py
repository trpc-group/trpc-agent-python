"""Select a sandbox provider from environment-backed configuration."""

import os

from .base import SandboxProvider
from .docker import DEFAULT_DOCKER_IMAGE
from .docker import DockerSandbox


def create_sandbox_provider(image: str | None = None) -> SandboxProvider:
    """Create the configured sandbox provider without starting a runtime."""
    backend = os.getenv("CODE_REVIEW_SANDBOX_BACKEND", "docker").strip().lower()
    if backend != "docker":
        raise ValueError(f"Unsupported sandbox backend: {backend}")
    selected_image = image or os.getenv(
        "CODE_REVIEW_DOCKER_IMAGE",
        DEFAULT_DOCKER_IMAGE,
    ).strip()
    if not selected_image:
        raise ValueError("Docker image must not be empty")

    def bounded_int(name: str, default: int) -> int:
        value = int(os.getenv(name, str(default)))
        if not 0 < value <= default:
            raise ValueError(f"{name} must be between 1 and {default}")
        return value

    return DockerSandbox(
        image=selected_image,
        memory_limit_bytes=bounded_int(
            "CODE_REVIEW_DOCKER_MEMORY_BYTES",
            512 * 1024 * 1024,
        ),
        nano_cpus=bounded_int("CODE_REVIEW_DOCKER_NANO_CPUS", 1_000_000_000),
        pids_limit=bounded_int("CODE_REVIEW_DOCKER_PIDS_LIMIT", 256),
        tmpfs_size_bytes=bounded_int(
            "CODE_REVIEW_DOCKER_TMPFS_BYTES",
            256 * 1024 * 1024,
        ),
        output_limit_bytes=bounded_int(
            "CODE_REVIEW_MAX_OUTPUT_BYTES",
            1024 * 1024,
        ),
    )
