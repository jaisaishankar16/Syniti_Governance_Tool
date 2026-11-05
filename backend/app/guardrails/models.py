"""Pydantic v2 data models for guardrails events, reports, verdicts, and exceptions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

SEVERITY_ORDER: dict[str, int] = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


class KeyValue(BaseModel):
    """Key-value pair for structured details in guardrail events."""

    model_config = ConfigDict(extra="forbid")

    key: str
    value: str


class GuardrailEvent(BaseModel):
    """An individual event emitted by a guardrail check."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    stage: Literal["input", "tool", "agent_output", "final_output", "budget"]
    action: Literal["pass", "warn", "sanitize", "repair", "block"]
    severity: Literal["info", "low", "medium", "high", "critical"]
    message: str
    details: list[KeyValue] = Field(default_factory=list)
    agent: str | None = None
    request_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GuardrailReport(BaseModel):
    """Comprehensive report summarizing all guardrail events in a pipeline run."""

    model_config = ConfigDict(extra="forbid")

    events: list[GuardrailEvent] = Field(default_factory=list)
    blocked: bool = False
    highest_severity: Literal["info", "low", "medium", "high", "critical"] | None = None
    counts_by_action: dict[str, int] = Field(default_factory=dict)

    def add_event(self, event: GuardrailEvent) -> None:
        """Adds an event, updating counts, blocked status, and highest severity."""
        self.events.append(event)
        self.counts_by_action[event.action] = self.counts_by_action.get(event.action, 0) + 1
        if event.action == "block":
            self.blocked = True

        if self.highest_severity is None:
            self.highest_severity = event.severity
        else:
            current_rank = SEVERITY_ORDER.get(self.highest_severity, 0)
            new_rank = SEVERITY_ORDER.get(event.severity, 0)
            if new_rank > current_rank:
                self.highest_severity = event.severity

    def merge(self, other: GuardrailReport) -> None:
        """Merges another report into this report."""
        for ev in other.events:
            self.add_event(ev)


class Verdict(BaseModel, Generic[T]):
    """Verdict wrapping the output of a guardrail check."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    value: T
    report: GuardrailReport


class GuardrailBlocked(Exception):
    """Exception raised when a safety-critical guardrail blocks execution."""

    def __init__(self, message: str, report: GuardrailReport) -> None:
        super().__init__(message)
        self.message = message
        self.report = report


class SanitizedInput(BaseModel):
    """Result of pre-flight input guardrail checks."""

    model_config = ConfigDict(extra="forbid")

    original_sql: str
    sanitized_sql: str
    untrusted_comments: list[str] = Field(default_factory=list)
    boundary_id: str
    canary_token: str
    secret_map: dict[str, str] = Field(default_factory=dict)
    injection_suspected: bool = False
    report: GuardrailReport = Field(default_factory=GuardrailReport)
