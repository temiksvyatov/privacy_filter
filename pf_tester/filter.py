"""Thin wrapper over the openai/privacy-filter HuggingFace pipeline."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import lru_cache
from typing import Iterable

DEFAULT_MODEL = "openai/privacy-filter"


@dataclass(frozen=True)
class Span:
    entity: str
    text: str
    start: int
    end: int
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


class PrivacyFilter:
    """Loads the model once and exposes detect / redact helpers."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str | int | None = None,
        aggregation_strategy: str = "simple",
    ) -> None:
        from transformers import pipeline

        self.model_name = model_name
        self.aggregation_strategy = aggregation_strategy
        self._pipe = pipeline(
            task="token-classification",
            model=model_name,
            aggregation_strategy=aggregation_strategy,
            device=device,
        )

    def detect(self, text: str) -> list[Span]:
        if not text:
            return []
        raw = self._pipe(text)
        spans: list[Span] = []
        for item in raw:
            entity = item.get("entity_group") or item.get("entity") or "UNKNOWN"
            spans.append(
                Span(
                    entity=entity,
                    text=item["word"],
                    start=int(item["start"]),
                    end=int(item["end"]),
                    score=float(item["score"]),
                )
            )
        return spans

    def redact(
        self,
        text: str,
        placeholder: str | None = None,
        spans: Iterable[Span] | None = None,
    ) -> str:
        """Replace detected PII spans with `[ENTITY]` (or a custom placeholder)."""
        spans = list(spans) if spans is not None else self.detect(text)
        if not spans:
            return text
        ordered = sorted(spans, key=lambda s: s.start)
        out: list[str] = []
        cursor = 0
        for s in ordered:
            if s.start < cursor:
                continue
            out.append(text[cursor:s.start])
            tag = placeholder if placeholder is not None else f"[{s.entity.upper()}]"
            out.append(tag)
            cursor = s.end
        out.append(text[cursor:])
        return "".join(out)


@lru_cache(maxsize=2)
def get_filter(model_name: str = DEFAULT_MODEL, device: str | int | None = None) -> PrivacyFilter:
    """Process-wide cached instance so we don't reload the model per request."""
    return PrivacyFilter(model_name=model_name, device=device)
