"""Run/job artifact writer with timestamped output directories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Any


@dataclass(frozen=True, slots=True)
class JobArtifactPaths:
    job_id: str
    job_dir: Path
    original_path: Path
    rewritten_path: Path
    diff_path: Path
    report_path: Path
    logs_path: Path


@dataclass(frozen=True, slots=True)
class RunPaths:
    run_id: str
    run_dir: Path
    jobs: dict[str, JobArtifactPaths]


def create_run_output_dir(output_root: str | Path, run_id: str) -> Path:
    """Create a unique, timestamped run directory; never overwrites existing."""

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)

    short_run_id = run_id[:8]
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    for suffix in ["", "_1", "_2", "_3", "_4", "_5"]:
        candidate = root / f"run_{ts}_{short_run_id}{suffix}"
        try:
            candidate.mkdir(parents=False, exist_ok=False)
            return candidate
        except FileExistsError:
            continue

    raise FileExistsError("Unable to create unique run directory after multiple attempts.")


def write_job_artifacts(
    *,
    output_root: str | Path,
    run_id: str,
    jobs: list[dict[str, Any]],
) -> RunPaths:
    """Write per-job artifacts and return structured output paths.

    Expected `jobs` entry keys:
    - job_id
    - original_source
    - rewritten_source
    - diff_patch
    - report (dict)
    - logs_text
    """

    run_dir = create_run_output_dir(output_root, run_id)

    result: dict[str, JobArtifactPaths] = {}
    for job in jobs:
        job_id = str(job["job_id"])
        job_dir = run_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=False)

        original_path = job_dir / "original.py"
        rewritten_path = job_dir / "rewritten.py"
        diff_path = job_dir / "diff.patch"
        report_path = job_dir / "report.json"
        logs_path = job_dir / "logs.txt"

        _atomic_write_text(original_path, str(job.get("original_source", "")))
        _atomic_write_text(rewritten_path, str(job.get("rewritten_source", "")))
        _atomic_write_text(diff_path, str(job.get("diff_patch", "")))
        _atomic_write_json(report_path, job.get("report", {}))
        _atomic_write_text(logs_path, str(job.get("logs_text", "")))

        result[job_id] = JobArtifactPaths(
            job_id=job_id,
            job_dir=job_dir,
            original_path=original_path,
            rewritten_path=rewritten_path,
            diff_path=diff_path,
            report_path=report_path,
            logs_path=logs_path,
        )

    return RunPaths(run_id=run_id, run_dir=run_dir, jobs=result)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        Path(tmp.name).replace(path)


def _atomic_write_json(path: Path, payload: Any) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    _atomic_write_text(path, serialized + "\n")
