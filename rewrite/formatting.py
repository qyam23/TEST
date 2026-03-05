"""Formatting helpers for rewritten source."""

from __future__ import annotations

from dataclasses import dataclass, field
import subprocess
import tempfile


@dataclass(frozen=True, slots=True)
class FormattingResult:
    formatted_source: str
    errors: list[str] = field(default_factory=list)


def format_rewritten_source(source: str) -> FormattingResult:
    """Run `black` then `ruff --fix` on rewritten source.

    Uses a temp file only for tool interoperability; caller passes/receives strings.
    """

    errors: list[str] = []
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".py", encoding="utf-8") as tmp:
        tmp.write(source)
        tmp.flush()

        _run_tool(["black", tmp.name], "black", errors)
        _run_tool(["ruff", "check", "--fix", tmp.name], "ruff", errors)

        tmp.seek(0)
        formatted = tmp.read()

    return FormattingResult(formatted_source=formatted, errors=errors)


def _run_tool(cmd: list[str], tool_name: str, errors: list[str]) -> None:
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        errors.append(f"{tool_name} is not installed.")
        return

    if completed.returncode != 0:
        errors.append(f"{tool_name} failed with exit code {completed.returncode}.")
        if completed.stdout.strip():
            errors.append(completed.stdout.strip())
        if completed.stderr.strip():
            errors.append(completed.stderr.strip())
