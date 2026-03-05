"""Core domain models for pipeline identity and schema-tracked artifacts.

Step 1 implementation: define canonical IDs (`file_hash`, `run_id`, `job_id`)
and `schema_version` in strongly-typed dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


SCHEMA_VERSION = "1.0"


class SandboxMode(str, Enum):
    """Validation execution mode, recorded as metadata (not pipeline state)."""

    SAFE = "SAFE"
    LIMITED = "LIMITED"
    FULL = "FULL"


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """Stable file identity used across runs and clustering."""

    path: str
    file_hash: str


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """Run-level identity and schema compatibility metadata."""

    run_id: str
    schema_version: str = SCHEMA_VERSION
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    invalidate_cache: bool = True


@dataclass(frozen=True, slots=True)
class JobIdentity:
    """Job identity that ties work back to run and canonical file hash."""

    job_id: str
    run_id: str
    file_hash: str
    cluster_id: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationMetadata:
    """Validation-side metadata captured in artifacts."""

    sandbox_mode: SandboxMode
    validated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
