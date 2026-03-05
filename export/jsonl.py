"""JSONL writer for per-job explainability records."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path
import tempfile
from typing import Any


@dataclass(frozen=True, slots=True)
class ExplainRecord:
    job_id: str
    file_hash: str
    run_id: str
    model_version: str
    schema_version: str
    sandbox_mode: str
    purpose: str
    key_funcs: list[str]
    risks: list[str]
    differences: list[str]


def write_explain_jsonl(run_dir: str | Path, records: list[ExplainRecord | dict[str, Any]]) -> Path:
    """Write one JSONL record per job into `<run_dir>/explain.jsonl` atomically."""

    target = Path(run_dir) / "explain.jsonl"
    lines: list[str] = []
    for rec in records:
        if isinstance(rec, ExplainRecord):
            payload = asdict(rec)
        else:
            payload = {
                "job_id": rec["job_id"],
                "file_hash": rec["file_hash"],
                "run_id": rec["run_id"],
                "model_version": rec["model_version"],
                "schema_version": rec["schema_version"],
                "sandbox_mode": rec["sandbox_mode"],
                "purpose": rec["purpose"],
                "key_funcs": rec["key_funcs"],
                "risks": rec["risks"],
                "differences": rec["differences"],
            }
        lines.append(json.dumps(payload, ensure_ascii=False))

    _atomic_write_text(target, ("\n".join(lines) + "\n") if lines else "")
    return target


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        Path(tmp.name).replace(path)
