"""Dependency detection based on AST import scanning."""

from __future__ import annotations

import ast
from pathlib import Path
import sys


def detect_third_party_dependencies(file_path: str) -> list[str]:
    """Return sorted unique third-party package names imported by a file.

    Stdlib and relative imports are excluded.
    """

    source = Path(file_path).read_text(encoding="utf-8")
    tree = ast.parse(source)

    candidates: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                candidates.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            if node.module:
                candidates.add(node.module.split(".")[0])

    stdlib = set(sys.stdlib_module_names)
    third_party = sorted(name for name in candidates if name and name not in stdlib)
    return third_party
