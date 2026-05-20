"""Pydantic v2 schemas for alerts, ground truth, and triage output.

See v1 spec § Data Model for canonical definitions.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from augur.data.enums import Disposition, Tactic


class Severity(StrEnum):
    """Severity for triage output."""

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class RawSignals(BaseModel):
    """Network/flow-level raw signals from an alert."""

    src_ip: str
    dst_ip: str
    dst_port: int
    protocol: str = "TCP"
    flow_duration_ms: int = 0
    packet_count: int = 0
    byte_count: int = 0
    flags: list[str] = Field(default_factory=list)


class AlertContext(BaseModel):
    """Contextual enrichment for an alert."""

    host_role: str = "unknown"
    user_account: str | None = None
    is_business_hours: bool = True


class Alert(BaseModel):
    """Canonical alert — what the Triage Agent receives (ground truth stripped)."""

    alert_id: UUID = Field(default_factory=uuid4)
    timestamp: str = Field(default="", description="ISO-8601 string")
    source: str = Field(
        default="synthetic", description="cicids2017 | cicids2018 | synthetic"
    )
    raw_signals: RawSignals
    detection_rule_fired: str = ""
    context: AlertContext = Field(default_factory=AlertContext)


class GroundTruth(BaseModel):
    """Ground truth label — paired with Alert for eval, NEVER shown to Triage Agent."""

    alert_id: UUID
    disposition: Disposition
    attack_tactic: Tactic | None = None
    attack_technique: str | None = None  # MITRE technique ID, e.g. T1021.002
    source: str = "synthetic"


class TriageOutput(BaseModel):
    """Structured triage report — what the Triage Agent emits."""

    alert_id: UUID
    disposition: Disposition
    attack_tactic: Tactic | None = Field(
        None, description="Not Applicable for FP dispositions"
    )
    attack_technique: str | None = None
    attack_technique_name: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    severity: Severity = Severity.MEDIUM
    recommended_action: str = ""
    reasoning: str = ""
    trace_id: str = ""

    @model_validator(mode="after")
    def _tactic_required_for_non_fp(self) -> TriageOutput:
        """Attack tactic must be set for any disposition except False Positive."""
        if (
            self.disposition != Disposition.FALSE_POSITIVE
            and self.attack_tactic is None
        ):
            raise ValueError(
                "attack_tactic is required except for False Positive disposition"
            )
        return self
