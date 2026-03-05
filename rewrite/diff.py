"""Unified diff generation for rewritten source."""

from __future__ import annotations

from dataclasses import dataclass
import difflib


@dataclass(frozen=True, slots=True)
class DiffStats:
    lines_added: int
    lines_removed: int
    percent_change: float


@dataclass(frozen=True, slots=True)
class DiffResult:
    diff: str
    stats: DiffStats


def generate_unified_diff(
    original_source: str,
    rewritten_source: str,
    *,
    fromfile: str = "original.py",
    tofile: str = "rewritten.py",
) -> DiffResult:
    """Generate unified diff and summary stats."""

    original_lines = original_source.splitlines(keepends=True)
    rewritten_lines = rewritten_source.splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            original_lines,
            rewritten_lines,
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )
    diff_text = "\n".join(diff_lines)

    added, removed = _count_changes(diff_lines)
    baseline = max(len(original_lines), 1)
    percent_change = ((added + removed) / baseline) * 100.0

    return DiffResult(
        diff=diff_text,
        stats=DiffStats(lines_added=added, lines_removed=removed, percent_change=percent_change),
    )


def _count_changes(diff_lines: list[str]) -> tuple[int, int]:
    added = 0
    removed = 0
    for line in diff_lines:
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed
