"""Rewrite package.

Avoid importing heavy submodules at package import time.
"""

__all__ = [
    "diff",
    "formatting",
    "rewriter",
]
