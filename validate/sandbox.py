"""Sandbox execution modes for validation.

SAFE: static-only validation.
LIMITED: run target via subprocess inside isolated tempdir with clean env.
FULL: not implemented (Docker mode deferred).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import multiprocessing as mp
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from .static import StaticValidationResult, run_static_validation


class SandboxMode(str, Enum):
    SAFE = "SAFE"
    LIMITED = "LIMITED"
    FULL = "FULL"


@dataclass(frozen=True, slots=True)
class SandboxValidationResult:
    mode: SandboxMode
    passed: bool
    errors: list[str] = field(default_factory=list)
    static: StaticValidationResult | None = None
    timed_out: bool = False
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""


def run_in_sandbox(
    file_path: str,
    *,
    mode: SandboxMode,
    timeout_s: float = 10.0,
) -> SandboxValidationResult:
    """Run validation for requested sandbox mode."""

    if mode == SandboxMode.SAFE:
        static_result = run_static_validation(file_path)
        errors = _flatten_static_errors(static_result)
        return SandboxValidationResult(
            mode=mode,
            passed=static_result.passed,
            errors=errors,
            static=static_result,
        )

    if mode == SandboxMode.FULL:
        raise NotImplementedError(
            "FULL sandbox mode is not implemented yet. "
            "Use SAFE or LIMITED until Docker-based isolation is added."
        )

    queue: mp.Queue[dict[str, Any]] = mp.Queue()
    proc = mp.Process(target=_limited_worker, args=(file_path, timeout_s, queue), daemon=True)
    proc.start()
    proc.join(timeout=timeout_s + 2.0)

    if proc.is_alive():
        proc.kill()
        proc.join()
        return SandboxValidationResult(
            mode=mode,
            passed=False,
            timed_out=True,
            errors=["LIMITED sandbox worker timed out."],
        )

    if queue.empty():
        return SandboxValidationResult(
            mode=mode,
            passed=False,
            errors=["LIMITED sandbox worker exited without result."],
        )

    payload = queue.get()
    static_payload = payload.pop("static", None)
    static_result = StaticValidationResult(**static_payload) if static_payload else None
    return SandboxValidationResult(static=static_result, **payload)


def _limited_worker(file_path: str, timeout_s: float, queue: mp.Queue[dict[str, Any]]) -> None:
    static_result = run_static_validation(file_path)
    if not static_result.passed:
        queue.put(
            {
                "mode": SandboxMode.LIMITED,
                "passed": False,
                "errors": _flatten_static_errors(static_result),
                "static": asdict(static_result),
                "timed_out": False,
                "returncode": None,
                "stdout": "",
                "stderr": "",
            }
        )
        return

    target = Path(file_path).resolve()
    minimal_env = {"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1"}

    with tempfile.TemporaryDirectory(prefix="validate-limited-") as tmp_dir:
        try:
            completed = subprocess.run(
                [sys.executable, str(target)],
                cwd=tmp_dir,
                env=minimal_env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
            errors = []
            if completed.returncode != 0:
                errors.append(f"Subprocess exited with code {completed.returncode}.")
                if completed.stderr.strip():
                    errors.append(completed.stderr.strip())

            queue.put(
                {
                    "mode": SandboxMode.LIMITED,
                    "passed": completed.returncode == 0,
                    "errors": errors,
                    "static": asdict(static_result),
                    "timed_out": False,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
            )
        except subprocess.TimeoutExpired as exc:
            queue.put(
                {
                    "mode": SandboxMode.LIMITED,
                    "passed": False,
                    "errors": [f"Subprocess timed out after {timeout_s}s."],
                    "static": asdict(static_result),
                    "timed_out": True,
                    "returncode": None,
                    "stdout": exc.stdout or "",
                    "stderr": exc.stderr or "",
                }
            )


def _flatten_static_errors(result: StaticValidationResult) -> list[str]:
    errors: list[str] = []
    for tool in result.tools:
        for issue in tool.errors:
            errors.append(f"[{tool.tool}] {issue}")
    return errors
