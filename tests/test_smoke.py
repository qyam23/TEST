"""End-to-end pipeline smoke test with a fake OpenAI client."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from core.models import SandboxMode
from core.pipeline import PipelineConfig, PipelineOrchestrator
from llm.client import OpenAIClientConfig, OpenAIJsonClient


class _FakeCompletions:
    def create(self, **kwargs):
        text = kwargs["messages"][-1]["content"]
        if "Analyze file" in text:
            payload = {
                "file_hash": "fake_hash",
                "language": "python",
                "findings": ["simple print"],
                "risks": [],
                "confidence": 0.98,
            }
        elif "Create a rewrite plan" in text:
            payload = {
                "file_hash": "fake_hash",
                "objective": "improve output",
                "steps": [{"step_id": "1", "description": "change print value"}],
                "safety_notes": [],
                "rewritten_source": "x = 2\nprint(x)\n",
            }
        else:
            payload = {
                "file_hash": "fake_hash",
                "status": "success",
                "changes_made": ["updated variable"],
                "warnings": [],
            }
        return {"choices": [{"message": {"content": json.dumps(payload)}}]}


class _FakeChat:
    completions = _FakeCompletions()


class _FakeOpenAI:
    chat = _FakeChat()


class SmokeTest(unittest.TestCase):
    def test_full_pipeline_single_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "sample.py"
            target.write_text("x = 1\nprint(x)\n# line3\n# line4\n# line5\n", encoding="utf-8")

            cfg = PipelineConfig(
                run_id="run_smoke_001",
                model_id="gpt-5",
                output_root=td,
                schema_version="1.0",
                max_workers=1,
                sandbox_mode=SandboxMode.SAFE,
                cache_dir=Path(td) / ".cache",
                rate_limit_pause_s=0.01,
            )

            client = OpenAIJsonClient(client=_FakeOpenAI(), config=OpenAIClientConfig(model="gpt-5"))
            orchestrator = PipelineOrchestrator(client=client, config=cfg, signals=None)

            # Keep this smoke test independent from optional parser dependency availability.
            orchestrator._parse_output = lambda payload, output_type: type(
                "_M", (), {"model_dump": lambda self: payload}
            )()
            # Keep smoke independent of local black/ruff installation.
            orchestrator._run_lint = lambda result: []
            # Keep smoke independent of local sandbox toolchain.
            orchestrator._run_validate = lambda result: []

            run_result = orchestrator.run([str(target)])
            self.assertEqual(run_result.run_id, "run_smoke_001")
            self.assertEqual(len(run_result.jobs), 1)
            job = next(iter(run_result.jobs.values()))
            self.assertEqual(job.job.state.value, "EXPORTED")
            self.assertTrue(run_result.explain_path.exists())
            self.assertTrue(run_result.manifest_path.exists())


if __name__ == "__main__":
    unittest.main()
