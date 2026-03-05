"""Qt signal definitions for UI/pipeline communication.

Step 4a: signals only. No widgets or pipeline execution logic.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class PipelineSignals(QObject):
    """Central signal bus used by background workers and UI layers.

    Signal contracts:
    - progress(job_id, stage, current, total)
    - log(level, message, job_id, stage)
    - artifact(job_id, artifact_type, artifact_path)
    - state_change(job_id, from_state, to_state)
    """

    progress = Signal(str, str, int, int)
    log = Signal(str, str, str, str)
    artifact = Signal(str, str, str)
    state_change = Signal(str, str, str)
