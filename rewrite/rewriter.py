"""Rewrite orchestration from analysis to validated rewrite output."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from llm.client import OpenAIJsonClient
from llm.parsers import OutputType, ParseError, RewritePlanOutput, parse_output
from llm.prompts.rewrite import build_rewrite_messages
from llm.prompts.types import FileJob


@dataclass(frozen=True, slots=True)
class RewriteResult:
    """Structured rewrite result returned by the rewrite orchestrator."""

    file_hash: str
    rewrite_plan: RewritePlanOutput
    rewritten_source: str
    raw_response: dict[str, Any]
    errors: list[str] = field(default_factory=list)


class RewriteOrchestrator:
    """Build request, call LLM client, validate output, and return structured result."""

    def __init__(self, client: OpenAIJsonClient) -> None:
        self._client = client

    def rewrite(self, *, analysis_output: dict[str, Any], file_job: FileJob) -> RewriteResult:
        """Execute rewrite request flow from analysis context and original source."""

        user_messages = build_rewrite_messages(file_job)
        analysis_context = json.dumps(analysis_output, ensure_ascii=False)
        user_messages[-1]["content"] += (
            "\n\nValidated analysis context (trusted pipeline artifact):\n"
            f"<ANALYSIS_JSON>{analysis_context}</ANALYSIS_JSON>\n"
            "Also include `rewritten_source` in the JSON response for downstream formatting/diff."
        )

        response = self._client.create_json_completion(messages=user_messages)
        payload = self._extract_payload(response)

        # Validate schema-defined rewrite plan fields through the parser allowlist.
        plan_payload = {
            key: payload.get(key)
            for key in ("file_hash", "objective", "steps", "safety_notes")
            if key in payload
        }
        rewrite_plan = parse_output(plan_payload, OutputType.REWRITE_PLAN)
        if not isinstance(rewrite_plan, RewritePlanOutput):
            raise ParseError("Rewrite parser returned unexpected model type.")

        rewritten_source = payload.get("rewritten_source")
        if not isinstance(rewritten_source, str) or not rewritten_source.strip():
            raise ParseError("Missing required `rewritten_source` string in rewrite response.")

        return RewriteResult(
            file_hash=file_job.file_hash,
            rewrite_plan=rewrite_plan,
            rewritten_source=rewritten_source,
            raw_response=payload,
        )

    @staticmethod
    def _extract_payload(response: Any) -> dict[str, Any]:
        """Extract JSON dict payload from chat completion response objects."""

        if isinstance(response, dict):
            if isinstance(response.get("choices"), list):
                content = response["choices"][0].get("message", {}).get("content", "")
                return RewriteOrchestrator._loads_json_obj(content)
            return response

        # OpenAI SDK object shape: response.choices[0].message.content
        choices = getattr(response, "choices", None)
        if choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", "") if message else ""
            return RewriteOrchestrator._loads_json_obj(content)

        raise ParseError("Unable to extract JSON payload from completion response.")

    @staticmethod
    def _loads_json_obj(content: str) -> dict[str, Any]:
        try:
            loaded = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ParseError("Rewrite response is not valid JSON.") from exc
        if not isinstance(loaded, dict):
            raise ParseError("Rewrite response JSON root must be an object.")
        return loaded
