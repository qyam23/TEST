"""Static validation utilities.

Runs:
- ast.parse
- ruff
- black --check

for a given file path and returns structured tool results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import ast
from pathlib import Path
import subprocess
from typing import Literal


ToolName = Literal["ast", "ruff", "black"]


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool: ToolName
    passed: bool
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StaticValidationResult:
    file_path: str
    passed: bool
    tools: list[ToolResult]


def run_static_validation(file_path: str) -> StaticValidationResult:
    """Run static checks and return a structured result."""

    target = Path(file_path)
    source = target.read_text(encoding="utf-8")

    tool_results = [
        _run_ast_check(source),
        _run_subprocess_tool("ruff", ["ruff", "check", str(target)]),
        _run_subprocess_tool("black", ["black", "--check", str(target)]),
    ]
    overall_passed = all(t.passed for t in tool_results)
    return StaticValidationResult(file_path=str(target), passed=overall_passed, tools=tool_results)


def _run_ast_check(source: str) -> ToolResult:
    try:
        ast.parse(source)
        return ToolResult(tool="ast", passed=True)
    except SyntaxError as exc:
        return ToolResult(tool="ast", passed=False, errors=[str(exc)])


def _run_subprocess_tool(tool: Literal["ruff", "black"], cmd: list[str]) -> ToolResult:
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return ToolResult(tool=tool, passed=False, errors=[f"{tool} is not installed."])

    errors: list[str] = []
    if completed.returncode != 0:
        if completed.stdout.strip():
            errors.append(completed.stdout.strip())
        if completed.stderr.strip():
            errors.append(completed.stderr.strip())

    return ToolResult(tool=tool, passed=completed.returncode == 0, errors=errors)
