"""Structured output parsers and guards for LLM JSON payloads.

Implements schema validation for:
- analysis
- rewrite_plan
- summary

Also enforces:
- schema allowlist (reject unknown fields)
- denylist keyword checks on all string values
"""

from __future__ import annotations

from enum import Enum
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


DENYLIST_KEYWORDS: tuple[str, ...] = (
    "ignore previous instructions",
    "system prompt",
    "developer message",
    "jailbreak",
    "exec(",
    "os.system",
    "subprocess",
    "curl ",
    "wget ",
    "rm -rf",
)


class OutputType(str, Enum):
    ANALYSIS = "analysis"
    REWRITE_PLAN = "rewrite_plan"
    SUMMARY = "summary"


class StrictSchemaModel(BaseModel):
    """Base model with allowlist-only field policy."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AnalysisOutput(StrictSchemaModel):
    file_hash: str
    language: str
    findings: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class RewriteStep(StrictSchemaModel):
    step_id: str
    description: str


class RewritePlanOutput(StrictSchemaModel):
    file_hash: str
    objective: str
    steps: list[RewriteStep] = Field(min_length=1)
    safety_notes: list[str] = Field(default_factory=list)


class SummaryOutput(StrictSchemaModel):
    file_hash: str
    status: str
    changes_made: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        allowed = {"success", "partial", "failed", "skipped"}
        if value not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return value


class ParseError(ValueError):
    """Raised when LLM output fails schema or policy checks."""


def _scan_for_denylist_strings(value: Any, denylist: tuple[str, ...]) -> None:
    if isinstance(value, str):
        lowered = value.lower()
        for token in denylist:
            if token in lowered:
                raise ParseError(f"Denylist keyword detected: {token!r}")
        return

    if isinstance(value, dict):
        for nested in value.values():
            _scan_for_denylist_strings(nested, denylist)
        return

    if isinstance(value, list):
        for nested in value:
            _scan_for_denylist_strings(nested, denylist)


def _load_payload(payload: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload

    try:
        loaded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ParseError("Invalid JSON payload.") from exc

    if not isinstance(loaded, dict):
        raise ParseError("Payload root must be a JSON object.")

    return loaded


def parse_output(
    payload: str | dict[str, Any],
    output_type: OutputType,
    *,
    denylist_keywords: tuple[str, ...] = DENYLIST_KEYWORDS,
) -> AnalysisOutput | RewritePlanOutput | SummaryOutput:
    """Parse and validate model output by declared type."""

    raw = _load_payload(payload)
    _scan_for_denylist_strings(raw, denylist_keywords)

    parser: type[BaseModel]
    if output_type == OutputType.ANALYSIS:
        parser = AnalysisOutput
    elif output_type == OutputType.REWRITE_PLAN:
        parser = RewritePlanOutput
    elif output_type == OutputType.SUMMARY:
        parser = SummaryOutput
    else:  # defensive for future enum expansion
        raise ParseError(f"Unsupported output type: {output_type}")

    try:
        return parser.model_validate(raw)
    except ValidationError as exc:
        raise ParseError(f"Schema validation failed: {exc}") from exc
