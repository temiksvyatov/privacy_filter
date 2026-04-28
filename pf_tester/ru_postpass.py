"""Russian-targeted regex post-pass.

Privacy Filter is trained predominantly on English data, so a number of
Russian-specific identifiers are missed or under-detected out of the box.
This module runs a deterministic regex sweep after the model and merges
its findings into the span list, mapping every match into the model's
existing taxonomy (so downstream code keeps using the same labels).

Coverage:

- private_person   — handled by the model, regex skipped (declension
                     patterns are too noisy for a flat regex).
- private_phone    — Russian formats: +7 / 8 / 7 with various separators.
- private_email    — generic email regex, augments model recall.
- private_url      — domain incl. Cyrillic TLDs (.рф, .москва).
- private_date     — DD.MM.YYYY, DD month-name YYYY (Russian month names).
- account_number   — passport (RU), SNILS, INN, OGRN/OGRNIP, credit
                     card numbers.
- secret           — generic API-key-looking tokens, AWS-style keys.
"""

from __future__ import annotations

import re
from typing import Iterable

from .filter import Span

# Confidence assigned to regex hits. Slightly below 1.0 so that overlap
# resolution prefers a real model hit when the boundaries match.
_REGEX_SCORE = 0.95

# Russian month names (nominative + genitive forms used in dates).
_RU_MONTHS = (
    r"янв(?:аря|\.)|фев(?:раля|\.)|мар(?:та|\.)|апр(?:еля|\.)|"
    r"мая|июн(?:я|\.)|июл(?:я|\.)|авг(?:уста|\.)|сен(?:тября|\.)|"
    r"окт(?:ября|\.)|ноя(?:бря|\.)|дек(?:абря|\.)"
)

# Helpers — \b doesn't always work nicely around Cyrillic, so we use
# explicit word-boundary lookarounds.
_WB_LEFT = r"(?<![\w\d])"
_WB_RIGHT = r"(?![\w\d])"

# Order matters: more specific / longer patterns run first so they "claim"
# the span before looser numeric rules can step on them. Overlap with both
# model spans and earlier regex hits is filtered downstream.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    # IBAN (very loose; matches common European/RU patterns) — must run
    # before credit-card so '3704 0044 0532 0130' inside an IBAN isn't
    # misclassified as a card number.
    (
        "account_number",
        re.compile(
            rf"{_WB_LEFT}[A-Z]{{2}}\d{{2}}(?:\s?[A-Z0-9]{{4}}){{3,7}}{_WB_RIGHT}",
        ),
    ),
    # Credit card: 13–19 digits with optional separators every 4.
    (
        "account_number",
        re.compile(
            rf"{_WB_LEFT}(?:\d{{4}}[\s\-]){{3}}\d{{4}}{_WB_RIGHT}|"
            rf"{_WB_LEFT}\d{{16}}{_WB_RIGHT}",
        ),
    ),
    # Russian phone: requires the leading +7/7/8 to actually be a phone
    # prefix — anchored by a left word-boundary plus a mandatory + or
    # whitespace so we don't grab the first 11 digits of a 12-digit INN.
    (
        "private_phone",
        re.compile(
            r"(?:(?<=^)|(?<=[\s,;:()\[\]<>\"']))"
            r"(?:\+7|8|7)"
            r"[\s\-\.\(\)]+\d{3}[\s\-\.\(\)]*\d{3}[\s\-\.]*\d{2}[\s\-\.]*\d{2}",
        ),
    ),
    (
        "private_email",
        re.compile(
            r"[A-Za-zА-Яа-я0-9._%+\-]+@[A-Za-zА-Яа-я0-9.\-]+\.[A-Za-zА-Яа-я]{2,}",
        ),
    ),
    (
        "private_url",
        re.compile(
            # http(s)://… or www.… ending before whitespace/quotes or
            # sentence-ending punctuation.
            r"(?:https?://|www\.)[^\s<>\"',;]+?(?=[\s<>\"',;]|[.!?](?:\s|$)|$)|"
            # Bare host with TLD, optional path. The path stops before
            # punctuation that's typically pure prose.
            r"[A-Za-zА-Яа-я0-9\-]+\.(?:рф|москва|com|ru|net|org|io|dev|info|biz)"
            r"(?:/[^\s<>\"',;]*?(?=[.!?](?:\s|$)|[\s<>\"',;]|$))?",
        ),
    ),
    (
        "private_date",
        re.compile(
            rf"{_WB_LEFT}\d{{1,2}}\.\d{{1,2}}\.\d{{2,4}}{_WB_RIGHT}|"
            rf"{_WB_LEFT}\d{{1,2}}\s+(?:{_RU_MONTHS})\s+\d{{4}}(?:\s*г\.?)?{_WB_RIGHT}",
            re.IGNORECASE,
        ),
    ),
    # OGRN/OGRNIP: 13 or 15 digits — runs before INN(10/12) and passport.
    (
        "account_number",
        re.compile(rf"{_WB_LEFT}\d{{15}}{_WB_RIGHT}|{_WB_LEFT}\d{{13}}{_WB_RIGHT}"),
    ),
    # INN: 12 (individual) or 10 (legal entity).
    (
        "account_number",
        re.compile(rf"{_WB_LEFT}\d{{12}}{_WB_RIGHT}|{_WB_LEFT}\d{{10}}{_WB_RIGHT}"),
    ),
    # Russian passport: "1234 567890" — requires the space.
    (
        "account_number",
        re.compile(rf"{_WB_LEFT}\d{{4}}\s\d{{6}}{_WB_RIGHT}"),
    ),
    # SNILS: "123-456-789 01".
    (
        "account_number",
        re.compile(rf"{_WB_LEFT}\d{{3}}-\d{{3}}-\d{{3}}[\s\-]\d{{2}}{_WB_RIGHT}"),
    ),
    # Generic secret-looking tokens (sk-..., AKIA..., long base64-ish).
    (
        "secret",
        re.compile(
            r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}\b|"
            r"\bAKIA[0-9A-Z]{16}\b|"
            r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}\b",
        ),
    ),
]


def _spans_overlap(a: Span, b_start: int, b_end: int) -> bool:
    return not (a.end <= b_start or b_end <= a.start)


def ru_postpass(text: str, spans: Iterable[Span]) -> list[Span]:
    """Augment model spans with regex matches without introducing duplicates."""
    existing = list(spans)
    additions: list[Span] = []

    for entity, rx in _RULES:
        for m in rx.finditer(text):
            start, end = m.start(), m.end()
            if any(_spans_overlap(s, start, end) for s in existing):
                continue
            if any(_spans_overlap(s, start, end) for s in additions):
                continue
            additions.append(
                Span(
                    entity=entity,
                    text=m.group(0),
                    start=start,
                    end=end,
                    score=_REGEX_SCORE,
                )
            )

    merged = existing + additions
    merged.sort(key=lambda s: s.start)
    return merged
