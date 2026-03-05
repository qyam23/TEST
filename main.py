"""Application entrypoint for the Codex Hardened desktop UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from core.models import SandboxMode
from core.pipeline import PipelineConfig, PipelineOrchestrator
from llm.client import OpenAIClientConfig, OpenAIJsonClient
from ui.main_window import MainWindow
from ui.pipeline_runner import PipelineRunner, RunnerConfig
from ui.signals import PipelineSignals


CONFIG_PATH = Path("config.yaml")


@dataclass(frozen=True, slots=True)
class AppConfig:
    model_id: str = "gpt-5"
    output_root: str = "outputs"
    cache_dir: str = ".cache"
    schema_version: str = "1.0"
    max_workers: int = 4
    sandbox_mode: str = "SAFE"
    rate_limit_pause_s: float = 15.0


def load_or_create_config(path: Path = CONFIG_PATH) -> AppConfig:
    if not path.exists():
        default = AppConfig()
        path.write_text(yaml.safe_dump(asdict(default), sort_keys=False), encoding="utf-8")
        return default

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig(
        model_id=str(raw.get("model_id", "gpt-5")),
        output_root=str(raw.get("output_root", "outputs")),
        cache_dir=str(raw.get("cache_dir", ".cache")),
        schema_version=str(raw.get("schema_version", "1.0")),
        max_workers=int(raw.get("max_workers", 4)),
        sandbox_mode=str(raw.get("sandbox_mode", "SAFE")),
        rate_limit_pause_s=float(raw.get("rate_limit_pause_s", 15.0)),
    )


class ConsentOpenAIClient:
    """Wrapper that requests consent before first remote API call."""

    def __init__(self, inner: OpenAIJsonClient, consent_cb: Callable[[], bool]) -> None:
        self._inner = inner
        self._consent_cb = consent_cb
        self._consent_granted = False

    def create_json_completion(self, **kwargs: Any) -> Any:
        if not self._consent_granted:
            if not self._consent_cb():
                raise RuntimeError("Remote API consent denied by user.")
            self._consent_granted = True
        return self._inner.create_json_completion(**kwargs)


def _build_pipeline_config(app_cfg: AppConfig) -> PipelineConfig:
    run_id = datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")
    return PipelineConfig(
        run_id=run_id,
        model_id=app_cfg.model_id,
        output_root=app_cfg.output_root,
        schema_version=app_cfg.schema_version,
        max_workers=app_cfg.max_workers,
        sandbox_mode=SandboxMode(app_cfg.sandbox_mode),
        cache_dir=app_cfg.cache_dir,
        rate_limit_pause_s=app_cfg.rate_limit_pause_s,
    )


def main() -> int:
    app = QApplication([])
    app_cfg = load_or_create_config()

    signals = PipelineSignals()
    window = MainWindow()
    window.show()

    def consent_dialog() -> bool:
        result = QMessageBox.question(
            window,
            "Remote API Consent",
            "Allow first remote OpenAI API request for this run?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return result == QMessageBox.StandardButton.Yes

    # The concrete SDK client must be injected by integration/bootstrap code.
    from core.pipeline import _NoopOpenAIClient  # local import to keep module load light

    base_client = OpenAIJsonClient(
        client=_NoopOpenAIClient(),
        config=OpenAIClientConfig(model=app_cfg.model_id),
    )
    client = ConsentOpenAIClient(base_client, consent_dialog)

    pipeline_config = _build_pipeline_config(app_cfg)
    orchestrator = PipelineOrchestrator(client=client, config=pipeline_config, signals=signals)

    file_paths, _ = QFileDialog.getOpenFileNames(window, "Select files to process")
    if file_paths:
        runner = PipelineRunner(
            orchestrator=orchestrator,
            signals=signals,
            config=RunnerConfig(file_paths=tuple(file_paths)),
        )
        runner.start()
        window._runner = runner  # keep reference alive for thread lifetime

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
