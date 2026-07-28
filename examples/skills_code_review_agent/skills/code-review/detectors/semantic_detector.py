"""Extension point for repository-aware or model-backed analysis."""

from __future__ import annotations

from typing import Any

from models.finding import Finding
from parser.diff_parser import ChangedFile

from .base import Detector


class SemanticDetector(Detector):
    def detect(self, changed_file: ChangedFile, rule: dict[str, Any]) -> list[Finding]:
        raise NotImplementedError("semantic detection requires a configured external analyzer")
