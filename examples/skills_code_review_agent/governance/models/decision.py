"""Intermediate analyzer result models."""

from enum import IntEnum

from pydantic import BaseModel

from ...agent.models import Decision


class RiskLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class AnalysisResult(BaseModel):
    decision: Decision = Decision.ALLOW
    risk_level: RiskLevel = RiskLevel.LOW
    reason: str = "Policy check passed."
    matched_rule: str = "allowed"
