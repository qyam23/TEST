"""Self-contained terminal demo for the hardened pipeline.

- Uses a hardcoded sample Python source.
- Uses a fake OpenAI client (no network/API key).
- Runs the full pipeline end-to-end.
- Prints live terminal visualization (DAG states, progress timing, logs, artifacts, diff stats).

Dependencies: stdlib + project modules only. No PySide6 usage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time
from typing import Any

from core.models import SandboxMode
from core.pipeline import PipelineConfig, PipelineOrchestrator, PipelineState
from llm.client import OpenAIClientConfig, OpenAIJsonClient
from rewrite.diff import generate_unified_diff


SAMPLE_SOURCE = """x = 1
print(x)
# hardcoded sample line 3
# hardcoded sample line 4
# hardcoded sample line 5
"""


STAGES = [
    PipelineState.LOADED.value,
    PipelineState.ANALYZED.value,
    PipelineState.REWRITTEN.value,
    PipelineState.LINTED.value,
    PipelineState.VALIDATED.value,
    PipelineState.SUMMARIZED.value,
    PipelineState.EXPORTED.value,
]


@dataclass
class _EmitProxy:
    callback: Any

    def emit(self, *args: Any) -> None:
        self.callback(*args)


class TerminalSignals:
    """Qt-like signal object with .emit() methods for orchestrator wiring."""

    def __init__(self) -> None:
        self.start_monotonic = time.monotonic()
        self.current_state: dict[str, str] = {}
        self.state_started: dict[tuple[str, str], float] = {}
        self.stage_timings: dict[str, dict[str, float]] = {}
        self.logs: list[str] = []

        self.progress = _EmitProxy(self.on_progress)
        self.log = _EmitProxy(self.on_log)
        self.artifact = _EmitProxy(self.on_artifact)
        self.state_change = _EmitProxy(self.on_state_change)

    def on_progress(self, job_id: str, stage: str, current: int, total: int) -> None:
        self._print_log("INFO", job_id, stage, f"progress {current}/{total}")

    def on_log(self, level: str, message: str, job_id: str, stage: str) -> None:
        self._print_log(level, job_id, stage, message)

    def on_artifact(self, job_id: str, artifact_type: str, artifact_path: str) -> None:
        self._print_log("INFO", job_id, "EXPORTED", f"artifact {artifact_type}: {artifact_path}")

    def on_state_change(self, job_id: str, from_state: str, to_state: str) -> None:
        now = time.monotonic()
        key = (job_id, to_state)
        self.current_state[job_id] = to_state
        self.state_started[key] = now
        self.stage_timings.setdefault(job_id, {})

        # Close prior stage timing if known.
        prev_key = (job_id, from_state)
        if prev_key in self.state_started:
            elapsed = now - self.state_started[prev_key]
            self.stage_timings[job_id][from_state] = elapsed

        self._print_dag(job_id)

    def _print_dag(self, job_id: str) -> None:
        state = self.current_state.get(job_id, PipelineState.LOADED.value)

        def icon(stage: str) -> str:
            if STAGES.index(stage) < STAGES.index(state):
                return "✅"
            if stage == state:
                return "🔄"
            return "⬜"

        parts = [f"{icon(stage)} {stage}" for stage in STAGES]
        print(f"\n[DAG] {job_id}: " + " -> ".join(parts))

    def _print_log(self, level: str, job_id: str, stage: str, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] level={level} job_id={job_id} stage={stage} msg={message}"
        self.logs.append(line)
        print(line)


class _FakeCompletions:
    def create(self, **kwargs: Any) -> dict[str, Any]:
        text = kwargs["messages"][-1]["content"]
        if "Analyze file" in text:
            payload = {
                "file_hash": "demo_hash",
                "language": "python",
                "findings": ["simple print usage"],
                "risks": ["none"],
                "confidence": 0.99,
            }
        elif "Create a rewrite plan" in text:
            payload = {
                "file_hash": "demo_hash",
                "objective": "improve output",
                "steps": [{"step_id": "1", "description": "increment value"}],
                "safety_notes": ["no dynamic execution"],
                "rewritten_source": "x = 2\nprint(x)\n# hardcoded sample line 3\n# hardcoded sample line 4\n# hardcoded sample line 5\n",
            }
        else:
            payload = {
                "file_hash": "demo_hash",
                "status": "success",
                "changes_made": ["updated x from 1 to 2"],
                "warnings": [],
            }
        return {"choices": [{"message": {"content": json.dumps(payload)}}]}


class _FakeChat:
    completions = _FakeCompletions()


class _FakeOpenAI:
    chat = _FakeChat()


def _install_demo_patches(orchestrator: PipelineOrchestrator) -> None:
    """Make the demo robust in environments lacking optional tools/deps."""

    orchestrator._parse_output = lambda payload, output_type: type(  # noqa: SLF001
        "_M", (), {"model_dump": lambda self: payload}
    )()

    def _demo_lint(result: Any) -> list[str]:
        diff_result = generate_unified_diff(result.original_source, result.rewritten_source)
        result.diff_patch = diff_result.diff
        result.report["diff_stats"] = {
            "lines_added": diff_result.stats.lines_added,
            "lines_removed": diff_result.stats.lines_removed,
            "percent_change": round(diff_result.stats.percent_change, 2),
        }
        return []

    orchestrator._run_lint = _demo_lint  # noqa: SLF001
    orchestrator._run_validate = lambda result: []  # noqa: SLF001


def main() -> int:
    print("=== Codex Hardened Pipeline Demo (Terminal) ===")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        sample_file = tmp / "demo_sample.py"
        sample_file.write_text(SAMPLE_SOURCE, encoding="utf-8")

        cfg = PipelineConfig(
            run_id="run_demo_001",
            model_id="gpt-5",
            output_root=tmp / "outputs",
            schema_version="1.0",
            max_workers=1,
            sandbox_mode=SandboxMode.SAFE,
            cache_dir=tmp / ".cache",
            rate_limit_pause_s=0.01,
        )

        signals = TerminalSignals()
        client = OpenAIJsonClient(client=_FakeOpenAI(), config=OpenAIClientConfig(model="gpt-5"))
        orchestrator = PipelineOrchestrator(client=client, config=cfg, signals=signals)
        _install_demo_patches(orchestrator)

        result = orchestrator.run([str(sample_file)])

        print("\n=== Final Artifacts Summary ===")
        for job_id, paths in result.run_paths.jobs.items():
            print(f"job={job_id}")
            print(f"  original.py  -> {paths.original_path}")
            print(f"  rewritten.py -> {paths.rewritten_path}")
            print(f"  diff.patch   -> {paths.diff_path}")
            print(f"  report.json  -> {paths.report_path}")
            print(f"  logs.txt     -> {paths.logs_path}")

        print(f"explain.jsonl -> {result.explain_path}")
        print(f"manifest.json -> {result.manifest_path}")

        print("\n=== Diff Stats ===")
        for job in result.jobs.values():
            stats = job.report.get("diff_stats", {})
            print(
                f"job={job.job.identity.job_id} added={stats.get('lines_added', 0)} "
                f"removed={stats.get('lines_removed', 0)} "
                f"percent_change={stats.get('percent_change', 0)}%"
            )

        print("\n=== Stage Timing (seconds) ===")
        for job_id, stage_map in signals.stage_timings.items():
            for stage in STAGES:
                if stage in stage_map:
                    print(f"job={job_id} stage={stage} dt={stage_map[stage]:.3f}")

    print("\nDemo completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
