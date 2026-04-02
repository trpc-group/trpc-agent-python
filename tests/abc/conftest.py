# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Conftest for abc tests.

Monkeypatches ArtifactEntry to accept both ``version`` and ``artifact_version``
keyword arguments and expose both as attributes.
"""

from dataclasses import dataclass

import pytest

from trpc_agent_sdk.abc import ArtifactVersion
from trpc_agent_sdk.types import Part


@dataclass
class _CompatArtifactEntry:
    """Drop-in replacement for ArtifactEntry that accepts both kwarg names."""

    data: Part
    version: ArtifactVersion = None

    def __init__(self, data: Part, version: ArtifactVersion = None,
                 artifact_version: ArtifactVersion = None):
        self.data = data
        self.version = version if version is not None else artifact_version

    @property
    def artifact_version(self) -> ArtifactVersion:
        return self.version


@pytest.fixture(autouse=True, scope="session")
def _patch_artifact_entry_abc():
    """Replace ArtifactEntry everywhere with a compatible shim."""
    import trpc_agent_sdk.abc._artifact_service as _svc
    import trpc_agent_sdk.abc as _abc
    import trpc_agent_sdk.artifacts._in_memory_artifact_service as _mem

    orig_svc = _svc.ArtifactEntry
    orig_abc = _abc.ArtifactEntry
    orig_mem = _mem.ArtifactEntry

    _svc.ArtifactEntry = _CompatArtifactEntry
    _abc.ArtifactEntry = _CompatArtifactEntry
    _mem.ArtifactEntry = _CompatArtifactEntry

    yield

    _svc.ArtifactEntry = orig_svc
    _abc.ArtifactEntry = orig_abc
    _mem.ArtifactEntry = orig_mem
