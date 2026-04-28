"""Thin wrapper over the openai/privacy-filter HuggingFace pipeline."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from functools import lru_cache
from typing import Iterable, Iterator

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
        num_threads: int | None = None,
    ) -> None:
        from transformers import pipeline

        self._tune_torch(num_threads)

        self.model_name = model_name
        self.aggregation_strategy = aggregation_strategy
        self._pipe = pipeline(
            task="token-classification",
            model=model_name,
            aggregation_strategy=aggregation_strategy,
            device=device,
        )
        # Make sure we never accumulate gradients or run dropout.
        try:
            self._pipe.model.eval()
        except AttributeError:
            pass

    @staticmethod
    def _tune_torch(num_threads: int | None) -> None:
        try:
            import torch
        except ImportError:
            return
        n = num_threads if num_threads is not None else int(os.getenv("PF_NUM_THREADS", "0"))
        if n > 0:
            torch.set_num_threads(n)

    @staticmethod
    @contextmanager
    def _no_grad() -> Iterator[None]:
        try:
            import torch
        except ImportError:
            yield
            return
        # `inference_mode` is a stricter, faster superset of `no_grad`.
        with torch.inference_mode():
            yield

    def detect(self, text: str, min_score: float = 0.0) -> list[Span]:
        if not text:
            return []
        with self._no_grad():
            raw = self._pipe(text)
        spans: list[Span] = []
        for item in raw:
            entity = item.get("entity_group") or item.get("entity") or "UNKNOWN"
            score = float(item["score"])
            if score < min_score:
                continue
            spans.append(
                Span(
                    entity=entity,
                    text=item["word"],
                    start=int(item["start"]),
                    end=int(item["end"]),
                    score=score,
                )
            )
        return spans

    def detect_batch(
        self, texts: list[str], min_score: float = 0.0, batch_size: int = 8
    ) -> list[list[Span]]:
        """Run inference on multiple texts in one forward pass per batch."""
        if not texts:
            return []
        with self._no_grad():
            raw_all = self._pipe(texts, batch_size=batch_size)
        out: list[list[Span]] = []
        for raw in raw_all:
            spans: list[Span] = []
            for item in raw:
                entity = item.get("entity_group") or item.get("entity") or "UNKNOWN"
                score = float(item["score"])
                if score < min_score:
                    continue
                spans.append(
                    Span(
                        entity=entity,
                        text=item["word"],
                        start=int(item["start"]),
                        end=int(item["end"]),
                        score=score,
                    )
                )
            out.append(spans)
        return out

    def redact(
        self,
        text: str,
        placeholder: str | None = None,
        spans: Iterable[Span] | None = None,
        mask_char: str | None = None,
    ) -> str:
        """Replace detected PII spans.

        Precedence:
          1. `mask_char` — repeat the char for the full span length
             (e.g. "Иванов" -> "******"). Handy for sanitizing logs while
             preserving layout.
          2. `placeholder` — replace each span with this exact string.
          3. default — typed tag like `[PRIVATE_PERSON]`.
        """
        spans = list(spans) if spans is not None else self.detect(text)
        if not spans:
            return text
        if mask_char is not None and len(mask_char) != 1:
            raise ValueError("mask_char must be a single character")
        ordered = sorted(spans, key=lambda s: s.start)
        out: list[str] = []
        cursor = 0
        for s in ordered:
            if s.start < cursor:
                continue
            out.append(text[cursor:s.start])
            if mask_char is not None:
                out.append(mask_char * (s.end - s.start))
            elif placeholder is not None:
                out.append(placeholder)
            else:
                out.append(f"[{s.entity.upper()}]")
            cursor = s.end
        out.append(text[cursor:])
        return "".join(out)


@lru_cache(maxsize=2)
def get_filter(model_name: str = DEFAULT_MODEL, device: str | int | None = None) -> PrivacyFilter:
    """Process-wide cached instance so we don't reload the model per request."""
    return PrivacyFilter(model_name=model_name, device=device)
