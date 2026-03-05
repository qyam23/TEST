"""QThread worker for pipeline orchestration.

Runs the core pipeline orchestrator in a background thread and never touches widgets.
"""

from __future__ import annotations

from dataclasses import dataclass
from PySide6.QtCore import QThread

from core.pipeline import PipelineOrchestrator, PipelineRunResult
from .signals import PipelineSignals


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    """Runtime options for pipeline execution thread."""

    file_paths: tuple[str, ...]


class PipelineRunner(QThread):
    """Background worker that executes the orchestrator and emits logs/signals."""

    def __init__(
        self,
        *,
        orchestrator: PipelineOrchestrator,
        signals: PipelineSignals,
        config: RunnerConfig,
    ) -> None:
        super().__init__()
        self._orchestrator = orchestrator
        self._signals = signals
        self._config = config
        self.result: PipelineRunResult | None = None
        self.error: str | None = None

    def run(self) -> None:
        try:
            self.result = self._orchestrator.run(list(self._config.file_paths))
            self._signals.log.emit("INFO", "Pipeline run completed", "*", "EXPORTED")
        except Exception as exc:  # thread boundary safety
            self.error = str(exc)
            self._signals.log.emit("ERR", f"Pipeline runner failed: {exc}", "*", "FAILED")
