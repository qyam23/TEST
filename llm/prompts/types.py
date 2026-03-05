"""Shared prompt context types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class FileJob:
    """Context object passed into prompt builders."""

    job_id: str
    run_id: str
    file_path: str
    file_hash: str
    language: str
    source_code: str
    schema_version: str = "1.0"
    extra_instructions: list[str] = field(default_factory=list)


def wrap_untrusted_code_xml(code: str, *, language: str, file_path: str) -> str:
    """Wrap untrusted source code in structural XML tags (§4.1)."""

    return (
        "<UNTRUSTED_CODE>\n"
        f"  <METADATA language=\"{language}\" path=\"{file_path}\" />\n"
        "  <CONTENT><![CDATA[\n"
        f"{code}\n"
        "]]></CONTENT>\n"
        "</UNTRUSTED_CODE>"
    )
