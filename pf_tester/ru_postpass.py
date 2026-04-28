"""Russian-targeted regex post-pass.

This module is a placeholder; the real rules are wired up in a later
commit. For now it just returns the spans unchanged.
"""

from __future__ import annotations

from typing import Iterable

from .filter import Span


def ru_postpass(text: str, spans: Iterable[Span]) -> list[Span]:
    return list(spans)
