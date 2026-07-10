from __future__ import annotations

import json
from pathlib import Path

from ..pipeline.models import CandidateRecord


class FixtureOptimizerBackend:
    def __init__(self, candidates_path: Path) -> None:
        self._candidates_path = candidates_path

    def load_candidates(self) -> list[CandidateRecord]:
        payload = json.loads(self._candidates_path.read_text(encoding="utf-8"))
        return [CandidateRecord.model_validate(item) for item in payload]
