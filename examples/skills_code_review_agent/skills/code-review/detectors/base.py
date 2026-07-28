"""Detector contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from models.finding import Finding
from parser.diff_parser import ChangedFile


class Detector(ABC):
    @abstractmethod
    def detect(self, changed_file: ChangedFile, rule: dict[str, Any]) -> list[Finding]:
        """Detect rule matches in added lines."""
