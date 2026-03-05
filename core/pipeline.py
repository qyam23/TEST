"""Pipeline state machine and orchestration layer."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Protocol

from core.models import JobIdentity, SandboxMode
from export.index import write_manifest
from export.jsonl import ExplainRecord, write_explain_jsonl
from export.writer import RunPaths, write_job_artifacts
from llm.client import OpenAIJsonClient, OpenAIClientConfig, RateLimitPause
from llm.prompts.analysis import build_analysis_messages, build_analysis_system_prompt
from llm.prompts.rewrite import build_rewrite_messages, build_rewrite_system_prompt
from llm.prompts.summary import build_summary_messages, build_summary_system_prompt
from llm.prompts.types import FileJob
from rewrite.diff import generate_unified_diff
from rewrite.formatting import format_rewritten_source
from storage.artifact_store import ArtifactCacheKey, ArtifactStore
from validate.sandbox import SandboxMode as ValidationSandboxMode
from validate.sandbox import run_in_sandbox


class _SignalEmitter(Protocol):
    def emit(self, *args: Any) -> None: ...


class _SignalBus(Protocol):
    progress: _SignalEmitter
    log: _SignalEmitter
    artifact: _SignalEmitter
    state_change: _SignalEmitter


class PipelineState(str, Enum):
    """Canonical job lifecycle states."""

    LOADED = "LOADED"
    ANALYZED = "ANALYZED"
    REWRITTEN = "REWRITTEN"
    LINTED = "LINTED"
    VALIDATED = "VALIDATED"
    SUMMARIZED = "SUMMARIZED"
    EXPORTED = "EXPORTED"
    FAILED = "FAILED"


_ALLOWED_TRANSITIONS: dict[PipelineState, set[PipelineState]] = {
    PipelineState.LOADED: {PipelineState.ANALYZED, PipelineState.FAILED},
    PipelineState.ANALYZED: {PipelineState.REWRITTEN, PipelineState.FAILED},
    PipelineState.REWRITTEN: {PipelineState.LINTED, PipelineState.FAILED},
    PipelineState.LINTED: {PipelineState.VALIDATED, PipelineState.FAILED},
    PipelineState.VALIDATED: {PipelineState.SUMMARIZED, PipelineState.FAILED},
    PipelineState.SUMMARIZED: {PipelineState.EXPORTED, PipelineState.FAILED},
    PipelineState.EXPORTED: set(),
    PipelineState.FAILED: set(),
}


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    from_state: PipelineState
    to_state: PipelineState
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str | None = None


@dataclass(slots=True)
class PipelineJob:
    identity: JobIdentity
    state: PipelineState = PipelineState.LOADED
    history: list[TransitionRecord] = field(default_factory=list)

    def can_transition_to(self, next_state: PipelineState) -> bool:
        return next_state in _ALLOWED_TRANSITIONS[self.state]

    def transition_to(self, next_state: PipelineState, reason: str | None = None) -> None:
        if not self.can_transition_to(next_state):
            raise ValueError(f"Invalid pipeline transition from {self.state.value} to {next_state.value}.")
        previous_state = self.state
        self.state = next_state
        self.history.append(TransitionRecord(from_state=previous_state, to_state=next_state, reason=reason))

    @property
    def is_terminal(self) -> bool:
        return self.state in {PipelineState.EXPORTED, PipelineState.FAILED}


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    run_id: str
    model_id: str
    output_root: str | Path
    schema_version: str = "1.0"
    max_workers: int = 4
    sandbox_mode: SandboxMode = SandboxMode.SAFE
    cache_dir: str | Path = ".cache"
    rate_limit_pause_s: float = 15.0


@dataclass(slots=True)
class PipelineJobResult:
    job: PipelineJob
    file_path: str
    original_source: str
    rewritten_source: str = ""
    diff_patch: str = ""
    report: dict[str, Any] = field(default_factory=dict)
    logs_text: str = ""
    errors: list[str] = field(default_factory=list)
    analysis: dict[str, Any] = field(default_factory=dict)
    rewrite_plan: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PipelineRunResult:
    run_id: str
    run_paths: RunPaths
    explain_path: Path
    manifest_path: Path
    jobs: dict[str, PipelineJobResult]


class PipelineOrchestrator:
    """Full pipeline orchestration on top of the existing state machine."""

    def __init__(self, *, client: OpenAIJsonClient, config: PipelineConfig, signals: _SignalBus | None = None) -> None:
        self._client = client
        self._config = config
        self._signals = signals
        self._store = ArtifactStore(cache_dir=config.cache_dir, current_schema_version=config.schema_version)
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._pause_lock = threading.Lock()

    def run(self, file_paths: list[str]) -> PipelineRunResult:
        jobs = self._create_jobs(file_paths)

        results: dict[str, PipelineJobResult] = {}
        with ThreadPoolExecutor(max_workers=max(self._config.max_workers, 1)) as executor:
            futures = {
                executor.submit(self._process_single_job, idx + 1, len(jobs), job_result): job_result
                for idx, job_result in enumerate(jobs)
            }
            for future in as_completed(futures):
                job_result = futures[future]
                try:
                    finished = future.result()
                except Exception as exc:  # defensive boundary
                    job_result.errors.append(str(exc))
                    self._fail_job(job_result, reason="worker_exception")
                    finished = job_result
                results[finished.job.identity.job_id] = finished

        run_paths, explain_path, manifest_path = self._export_results(results)
        return PipelineRunResult(
            run_id=self._config.run_id,
            run_paths=run_paths,
            explain_path=explain_path,
            manifest_path=manifest_path,
            jobs=results,
        )

    def _create_jobs(self, file_paths: list[str]) -> list[PipelineJobResult]:
        created: list[PipelineJobResult] = []
        for idx, file_path in enumerate(file_paths, start=1):
            source = Path(file_path).read_text(encoding="utf-8")
            file_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
            job_id = f"job_{idx:04d}"
            identity = JobIdentity(job_id=job_id, run_id=self._config.run_id, file_hash=file_hash)
            created.append(
                PipelineJobResult(
                    job=PipelineJob(identity=identity),
                    file_path=file_path,
                    original_source=source,
                )
            )
            self._emit_log("INFO", "Loaded file", job_id, PipelineState.LOADED.value)
        return created

    def _process_single_job(self, index: int, total: int, result: PipelineJobResult) -> PipelineJobResult:
        try:
            self._transition(result, PipelineState.ANALYZED)
            result.analysis = self._run_analysis(result)
            self._emit_progress(result, index, total)

            self._transition(result, PipelineState.REWRITTEN)
            result.rewrite_plan, result.rewritten_source = self._run_rewrite(result)
            self._emit_progress(result, index, total)

            self._transition(result, PipelineState.LINTED)
            lint_errors = self._run_lint(result)
            if lint_errors:
                result.errors.extend(lint_errors)
                self._fail_job(result, reason="lint_failed")
                return result
            self._emit_progress(result, index, total)

            self._transition(result, PipelineState.VALIDATED)
            validation_errors = self._run_validate(result)
            if validation_errors:
                result.errors.extend(validation_errors)
                self._fail_job(result, reason="validation_failed")
                return result
            self._emit_progress(result, index, total)

            self._transition(result, PipelineState.SUMMARIZED)
            result.summary = self._run_summary(result)
            self._emit_progress(result, index, total)
            return result

        except Exception as exc:
            result.errors.append(str(exc))
            self._fail_job(result, reason="pipeline_exception")
            return result

    def _run_analysis(self, result: PipelineJobResult) -> dict[str, Any]:
        file_job = self._file_job(result, source_code=result.original_source)
        messages = build_analysis_messages(file_job)
        cache_key = self._cache_key(result.job.identity.file_hash, build_analysis_system_prompt(schema_version=file_job.schema_version))

        cached = self._store.get(cache_key)
        if cached is not None:
            self._emit_log("INFO", "Analysis cache hit", result.job.identity.job_id, result.job.state.value)
            return cached

        payload = self._call_json_completion(messages)
        parsed = self._parse_output(payload, "analysis")
        out = self._model_to_dict(parsed)
        self._store.set(cache_key, out)
        return out

    def _run_rewrite(self, result: PipelineJobResult) -> tuple[dict[str, Any], str]:
        file_job = self._file_job(result, source_code=result.original_source)
        messages = build_rewrite_messages(file_job)
        messages[-1]["content"] += "\n\nAlso include `rewritten_source` in the JSON response."
        cache_key = self._cache_key(result.job.identity.file_hash, build_rewrite_system_prompt(schema_version=file_job.schema_version))

        cached = self._store.get(cache_key)
        if cached is None:
            payload = self._call_json_completion(messages)
            self._store.set(cache_key, payload)
            cached = payload
        else:
            self._emit_log("INFO", "Rewrite cache hit", result.job.identity.job_id, result.job.state.value)

        plan_payload = {
            k: cached.get(k)
            for k in ("file_hash", "objective", "steps", "safety_notes")
            if k in cached
        }
        plan = self._model_to_dict(self._parse_output(plan_payload, "rewrite_plan"))
        rewritten_source = cached.get("rewritten_source")
        if not isinstance(rewritten_source, str) or not rewritten_source.strip():
            raise ValueError("rewrite response missing `rewritten_source` string")
        return plan, rewritten_source

    def _run_lint(self, result: PipelineJobResult) -> list[str]:
        formatted = format_rewritten_source(result.rewritten_source)
        result.rewritten_source = formatted.formatted_source
        result.diff_patch = generate_unified_diff(result.original_source, result.rewritten_source).diff
        return formatted.errors

    def _run_validate(self, result: PipelineJobResult) -> list[str]:
        mode = self._to_validation_mode(self._config.sandbox_mode)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", encoding="utf-8") as tmp:
            tmp.write(result.rewritten_source)
            tmp.flush()
            validation = run_in_sandbox(tmp.name, mode=mode)

        result.report["validation"] = {
            "passed": validation.passed,
            "errors": validation.errors,
            "timed_out": validation.timed_out,
            "returncode": validation.returncode,
            "sandbox_mode": mode.value,
        }
        return validation.errors

    def _run_summary(self, result: PipelineJobResult) -> dict[str, Any]:
        file_job = self._file_job(result, source_code=result.rewritten_source)
        messages = build_summary_messages(file_job)
        cache_key = self._cache_key(result.job.identity.file_hash, build_summary_system_prompt(schema_version=file_job.schema_version))

        cached = self._store.get(cache_key)
        if cached is not None:
            self._emit_log("INFO", "Summary cache hit", result.job.identity.job_id, result.job.state.value)
            return cached

        payload = self._call_json_completion(messages)
        parsed = self._parse_output(payload, "summary")
        out = self._model_to_dict(parsed)
        self._store.set(cache_key, out)
        return out

    def _export_results(self, results: dict[str, PipelineJobResult]) -> tuple[RunPaths, Path, Path]:
        jobs_payload = []
        for item in results.values():
            item.logs_text = "\n".join(item.errors)
            jobs_payload.append(
                {
                    "job_id": item.job.identity.job_id,
                    "original_source": item.original_source,
                    "rewritten_source": item.rewritten_source,
                    "diff_patch": item.diff_patch,
                    "report": {
                        "analysis": item.analysis,
                        "rewrite_plan": item.rewrite_plan,
                        "summary": item.summary,
                        "errors": item.errors,
                        **item.report,
                    },
                    "logs_text": item.logs_text,
                }
            )

        run_paths = write_job_artifacts(
            output_root=self._config.output_root,
            run_id=self._config.run_id,
            jobs=jobs_payload,
        )

        explain_records: list[ExplainRecord] = []
        manifest_jobs: list[dict[str, Any]] = []
        for job_id, item in results.items():
            artifact_paths = run_paths.jobs[job_id]
            explain_records.append(
                ExplainRecord(
                    job_id=job_id,
                    file_hash=item.job.identity.file_hash,
                    run_id=self._config.run_id,
                    model_version=self._config.model_id,
                    schema_version=self._config.schema_version,
                    sandbox_mode=self._config.sandbox_mode.value,
                    purpose="rewrite",
                    key_funcs=[],
                    risks=item.analysis.get("risks", []) if isinstance(item.analysis, dict) else [],
                    differences=[item.diff_patch[:500]] if item.diff_patch else [],
                )
            )

            manifest_jobs.append(
                {
                    "job_id": job_id,
                    "final_state": item.job.state.value,
                    "cluster_id": item.job.identity.cluster_id,
                    "artifacts": {
                        "original.py": str(artifact_paths.original_path),
                        "rewritten.py": str(artifact_paths.rewritten_path),
                        "diff.patch": str(artifact_paths.diff_path),
                        "report.json": str(artifact_paths.report_path),
                        "logs.txt": str(artifact_paths.logs_path),
                    },
                }
            )

            if item.job.state == PipelineState.SUMMARIZED:
                self._transition(item, PipelineState.EXPORTED)
            for artifact_name, artifact_path in manifest_jobs[-1]["artifacts"].items():
                self._emit_artifact(job_id, artifact_name, artifact_path)

        explain_path = write_explain_jsonl(run_paths.run_dir, explain_records)
        manifest_path = write_manifest(run_paths.run_dir, manifest_jobs)
        return run_paths, explain_path, manifest_path

    def _call_json_completion(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        while True:
            self._pause_event.wait()
            try:
                response = self._client.create_json_completion(messages=messages)
                return self._extract_payload(response)
            except RateLimitPause:
                with self._pause_lock:
                    if self._pause_event.is_set():
                        self._pause_event.clear()
                        self._emit_log("WARN", "Global rate-limit pause", "*", "REWRITTEN")
                        time.sleep(self._config.rate_limit_pause_s)
                        self._pause_event.set()
                continue

    @staticmethod
    def _extract_payload(response: Any) -> dict[str, Any]:
        if isinstance(response, dict):
            if isinstance(response.get("choices"), list):
                raw = response["choices"][0].get("message", {}).get("content", "{}")
                loaded = json.loads(raw)
                if not isinstance(loaded, dict):
                    raise ValueError("LLM response content must decode to JSON object")
                return loaded
            return response

        choices = getattr(response, "choices", None)
        if choices:
            msg = getattr(choices[0], "message", None)
            content = getattr(msg, "content", "{}") if msg else "{}"
            loaded = json.loads(content)
            if not isinstance(loaded, dict):
                raise ValueError("LLM response content must decode to JSON object")
            return loaded

        raise ValueError("Unsupported completion response shape")

    def _parse_output(self, payload: dict[str, Any], output_type: str) -> Any:
        from llm.parsers import OutputType, parse_output  # lazy to tolerate optional deps during import

        return parse_output(payload, OutputType(output_type))

    @staticmethod
    def _model_to_dict(model: Any) -> dict[str, Any]:
        if hasattr(model, "model_dump"):
            return model.model_dump()
        if isinstance(model, dict):
            return model
        raise ValueError("Unable to normalize model output to dict")

    def _cache_key(self, file_hash: str, system_prompt: str) -> ArtifactCacheKey:
        return ArtifactCacheKey(
            model_id=self._config.model_id,
            system_prompt_hash=hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
            file_hash=file_hash,
            schema_version=self._config.schema_version,
        )

    def _file_job(self, result: PipelineJobResult, *, source_code: str) -> FileJob:
        return FileJob(
            job_id=result.job.identity.job_id,
            run_id=self._config.run_id,
            file_path=result.file_path,
            file_hash=result.job.identity.file_hash,
            language=self._detect_language(result.file_path),
            source_code=source_code,
            schema_version=self._config.schema_version,
        )

    @staticmethod
    def _detect_language(file_path: str) -> str:
        suffix = Path(file_path).suffix.lower()
        if suffix == ".py":
            return "python"
        return suffix.lstrip(".") or "text"

    @staticmethod
    def _to_validation_mode(mode: SandboxMode) -> ValidationSandboxMode:
        if mode == SandboxMode.SAFE:
            return ValidationSandboxMode.SAFE
        if mode == SandboxMode.LIMITED:
            return ValidationSandboxMode.LIMITED
        return ValidationSandboxMode.FULL

    def _transition(self, result: PipelineJobResult, next_state: PipelineState, *, reason: str | None = None) -> None:
        previous = result.job.state
        result.job.transition_to(next_state, reason=reason)
        self._emit_state_change(result.job.identity.job_id, previous.value, next_state.value)

    def _fail_job(self, result: PipelineJobResult, *, reason: str) -> None:
        if result.job.state != PipelineState.FAILED and result.job.can_transition_to(PipelineState.FAILED):
            self._transition(result, PipelineState.FAILED, reason=reason)
        self._emit_log("ERR", reason, result.job.identity.job_id, result.job.state.value)

    def _emit_progress(self, result: PipelineJobResult, current: int, total: int) -> None:
        if self._signals:
            self._signals.progress.emit(result.job.identity.job_id, result.job.state.value, current, total)

    def _emit_log(self, level: str, message: str, job_id: str, stage: str) -> None:
        if self._signals:
            self._signals.log.emit(level, message, job_id, stage)

    def _emit_state_change(self, job_id: str, from_state: str, to_state: str) -> None:
        if self._signals:
            self._signals.state_change.emit(job_id, from_state, to_state)

    def _emit_artifact(self, job_id: str, artifact_type: str, artifact_path: str) -> None:
        if self._signals:
            self._signals.artifact.emit(job_id, artifact_type, artifact_path)


def allowed_transitions_for(state: PipelineState) -> tuple[PipelineState, ...]:
    return tuple(sorted(_ALLOWED_TRANSITIONS[state], key=lambda s: s.value))


def build_default_orchestrator(*, config: PipelineConfig, signals: _SignalBus | None = None) -> PipelineOrchestrator:
    client = OpenAIJsonClient(client=_NoopOpenAIClient(), config=OpenAIClientConfig(model=config.model_id))
    return PipelineOrchestrator(client=client, config=config, signals=signals)


class _NoopCompletions:
    def create(self, **_: Any) -> Any:
        raise RuntimeError("No OpenAI client provided. Inject a configured SDK client.")


class _NoopChat:
    completions = _NoopCompletions()


class _NoopOpenAIClient:
    chat = _NoopChat()
