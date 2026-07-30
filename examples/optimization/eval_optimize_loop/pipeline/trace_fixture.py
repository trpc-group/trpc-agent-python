"""Hash-pinned trace fixture contract shared by preflight and replay."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.evaluation import EvalSet

from .models import Phase, Split
from .schema import parse_strict_json


class TraceFixture:
    """Parse, pin and validate every phase/split in a trace fixture."""

    def __init__(
        self,
        path: str | Path,
        dataset_hashes: dict[str, str],
        expected_hash: Optional[str] = None,
    ) -> None:
        self.path = Path(path)
        self.dataset_hashes = dict(dataset_hashes)
        if not self.path.is_file():
            raise FileNotFoundError(f"trace fixture does not exist: {self.path}")
        content = self.path.read_bytes()
        self.sha256 = hashlib.sha256(content).hexdigest()
        if expected_hash is not None and self.sha256 != expected_hash:
            raise ValueError("trace fixture changed after preflight")
        self.payload = parse_strict_json(content.decode("utf-8"))

    def validate(self, train: EvalSet, validation: EvalSet) -> None:
        for phase in Phase:
            self.eval_set(train, Split.TRAIN, phase)
            self.eval_set(validation, Split.VALIDATION, phase)

    def eval_set(self, eval_set: EvalSet, split: Split, phase: Phase) -> EvalSet:
        if self.payload.get("schemaVersion") != "v1":
            raise ValueError("unsupported trace fixture schemaVersion")
        if self.payload.get("datasetHashes") != self.dataset_hashes:
            raise ValueError("trace fixture dataset hashes do not match validated inputs")
        try:
            cases = self.payload["phases"][phase.value][split.value]
        except (KeyError, TypeError) as error:
            raise ValueError(f"trace fixture is missing {phase.value}/{split.value}") from error
        expected_ids = [case.eval_id for case in eval_set.eval_cases]
        if not isinstance(cases, dict) or set(cases) != set(expected_ids):
            raise ValueError("trace fixture case IDs do not match the dataset")
        raw = eval_set.model_dump(mode="json", by_alias=True, exclude_none=True)
        for case in raw["evalCases"]:
            conversation = deepcopy(cases[case["evalId"]])
            if not isinstance(conversation, list) or not conversation:
                raise ValueError("each trace fixture conversation must be non-empty")
            case["evalMode"] = "trace"
            case["actualConversation"] = conversation
        return EvalSet.model_validate(raw)
