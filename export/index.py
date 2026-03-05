"""Manifest index writer for run outputs."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any


def write_manifest(run_dir: str | Path, jobs: list[dict[str, Any]]) -> Path:
    """Write `manifest.json` at run root with final state/artifacts/cluster metadata.

    Each job entry should include:
    - job_id
    - final_state
    - cluster_id
    - artifacts (dict of artifact name -> path)
    """

    run_path = Path(run_dir)
    target = run_path / "manifest.json"

    payload = {
        "run_dir": str(run_path),
        "jobs": [
            {
                "job_id": str(job["job_id"]),
                "final_state": str(job["final_state"]),
                "cluster_id": job.get("cluster_id"),
                "artifacts": dict(job.get("artifacts", {})),
            }
            for job in jobs
        ],
    }

    _atomic_write_text(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return target


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        Path(tmp.name).replace(path)
