"""Disk-backed artifact cache for LLM response payloads.

Cache key dimensions:
- model_id
- system_prompt_hash
- file_hash
- schema_version

Storage format: JSON files under `.cache/` with atomic writes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any


@dataclass(frozen=True, slots=True)
class ArtifactCacheKey:
    model_id: str
    system_prompt_hash: str
    file_hash: str
    schema_version: str


class ArtifactStore:
    """Persistent local artifact cache (disk-only, no in-memory layer)."""

    def __init__(
        self,
        *,
        cache_dir: str | Path = ".cache",
        current_schema_version: str = "1.0",
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._current_schema_version = current_schema_version

    def get(self, key: ArtifactCacheKey) -> dict[str, Any] | None:
        """Load cached payload by key.

        On schema mismatch or malformed file, returns None and does not crash.
        """

        path = self._path_for_key(key)
        if not path.exists():
            return None

        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if not isinstance(doc, dict):
            return None

        metadata = doc.get("metadata")
        payload = doc.get("payload")
        if not isinstance(metadata, dict) or not isinstance(payload, dict):
            return None

        # Invalidate-on-schema-mismatch policy: treat as cache miss, don't crash.
        cached_schema = metadata.get("schema_version")
        if cached_schema != self._current_schema_version:
            self._safe_unlink(path)
            return None

        key_dict = asdict(key)
        for k, v in key_dict.items():
            if metadata.get(k) != v:
                return None

        return payload

    def set(self, key: ArtifactCacheKey, payload: dict[str, Any]) -> None:
        """Persist payload for key using atomic file replacement."""

        path = self._path_for_key(key)
        envelope = {
            "metadata": asdict(key),
            "payload": payload,
        }
        self._atomic_write_json(path, envelope)

    def invalidate(self, file_hash: str) -> int:
        """Remove all cached entries for the given file hash.

        Returns the number of removed entries.
        """

        removed = 0
        for path in self._cache_dir.glob("*.json"):
            metadata = self._read_metadata(path)
            if metadata and metadata.get("file_hash") == file_hash:
                self._safe_unlink(path)
                removed += 1
        return removed

    def invalidate_all(self) -> int:
        """Clear all cache entries. Returns number of removed files."""

        removed = 0
        for path in self._cache_dir.glob("*.json"):
            self._safe_unlink(path)
            removed += 1
        return removed

    def _path_for_key(self, key: ArtifactCacheKey) -> Path:
        fingerprint = hashlib.sha256(
            "|".join(
                [
                    key.model_id,
                    key.system_prompt_hash,
                    key.file_hash,
                    key.schema_version,
                ]
            ).encode("utf-8")
        ).hexdigest()
        return self._cache_dir / f"{fingerprint}.json"

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as tmp:
            tmp.write(text)
            tmp.flush()
            Path(tmp.name).replace(path)

    @staticmethod
    def _read_metadata(path: Path) -> dict[str, Any] | None:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        metadata = doc.get("metadata") if isinstance(doc, dict) else None
        return metadata if isinstance(metadata, dict) else None

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
