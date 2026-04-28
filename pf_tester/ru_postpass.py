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
- private_url      — domain incl. Cyrillic TLDs (.рф / .москва / .рус /
                     .онлайн) and modern gTLDs (.app, .ai, .xyz, …).
- private_date     — DD.MM.YYYY, DD month-name YYYY (Russian month names).
- account_number   — passport (RU), SNILS, INN, OGRN/OGRNIP, credit
                     card numbers. Strict mode requires a Russian
                     context anchor (`ИНН`, `ОГРН`, …) to suppress
                     bare-number false positives.
- secret           — generic API-key-looking tokens, AWS-style keys.

`ru_postpass(text, spans, strict=False)`:
- `strict=False` (default): preserves prior behaviour — bare 10/12/13/15
  digit numbers are flagged as `account_number`. Best for free-form
  prose where false positives are acceptable.
- `strict=True`: bare numeric account numbers are only flagged when a
  context keyword (`ИНН`, `ОГРН`, `СНИЛС`, `паспорт`, …) appears
  immediately before the number. Use for noisy inputs (logs, code,
  catalogues) where 10–15 digit IDs are normal and unrelated to PII.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .filter import Entity, Span

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

# TLDs we'll accept on bare hosts (without scheme). Kept reasonably tight
# to avoid claiming everything that looks like `something.foo` — see
# README §"URL detection caveats" for context.
_TLDS = (
    # ccTLDs we care about.
    "ru", "by", "kz", "ua", "uk",
    # Cyrillic.
    "рф", "москва", "рус", "онлайн",
    # gTLDs (legacy + modern).
    "com", "net", "org", "info", "biz", "edu", "gov",
    "io", "dev", "app", "ai", "co", "me", "tech", "xyz",
    "cloud", "site", "store", "page",
)
_TLD_GROUP = "|".join(re.escape(t) for t in _TLDS)


# Context keywords that flip on strict-mode account-number rules. Each one
# may immediately precede the number (with a colon or whitespace between).
_ACCOUNT_CTX = (
    r"(?:"
    r"ИНН|инн|INN|"
    r"ОГРН(?:ИП)?|огрн(?:ип)?|OGRN(?:IP)?|"
    r"СНИЛС|снилс|SNILS|"
    r"паспорт|passport|"
    r"номер\s+карты|card\s+(?:no|number)"
    r")[\s:№#]*"
)


# --- entity-level rule classes ---------------------------------------------
# Each entry is (Entity, list[regex]). At import time we union the regexes
# inside an alternation per entity (P7), which gives one `re.finditer` pass
# per entity instead of one per pattern. Order between entities still
# matters because overlap resolution is left-to-right; see comments below.

@dataclass(frozen=True)
class _RuleSet:
    entity: Entity
    pattern: re.Pattern[str]


def _compile(entity: Entity, *parts: str, flags: int = 0) -> _RuleSet:
    """Compile an alternation for one entity."""
    return _RuleSet(entity=entity, pattern=re.compile("|".join(parts), flags))


# IBAN must run before credit-card so the embedded card-shaped run inside
# an IBAN doesn't get re-classified.
_IBAN = rf"{_WB_LEFT}[A-Z]{{2}}\d{{2}}(?:\s?[A-Z0-9]{{4}}){{3,7}}{_WB_RIGHT}"

# Credit card: 13–19 digits with optional separators every 4.
_CREDIT_CARD = (
    rf"{_WB_LEFT}(?:\d{{4}}[\s\-]){{3}}\d{{4}}{_WB_RIGHT}|"
    rf"{_WB_LEFT}\d{{16}}{_WB_RIGHT}"
)

# Russian phone: +7/7/8 + 10 digits with various separators. Anchored by a
# left word-boundary plus mandatory non-digit prefix so we don't grab the
# first 11 digits of a 12-digit INN.
_RU_PHONE = (
    r"(?:(?<=^)|(?<=[\s,;:()\[\]<>\"']))"
    r"(?:\+7|8|7)"
    r"[\s\-\.\(\)]+\d{3}[\s\-\.\(\)]*\d{3}[\s\-\.]*\d{2}[\s\-\.]*\d{2}"
)

_EMAIL = r"[A-Za-zА-Яа-я0-9._%+\-]+@[A-Za-zА-Яа-я0-9.\-]+\.[A-Za-zА-Яа-я]{2,}"

# URLs:
#   1. http(s)://… or www.… ending before whitespace/quotes/punct.
#   2. bare host with TLD from `_TLDS`, optional path.
_URL = (
    r"(?:https?://|www\.)[^\s<>\"',;]+?(?=[\s<>\"',;]|[.!?](?:\s|$)|$)|"
    rf"[A-Za-zА-Яа-я0-9\-]+\.(?:{_TLD_GROUP})"
    r"(?:/[^\s<>\"',;]*?(?=[.!?](?:\s|$)|[\s<>\"',;]|$))?"
)

_RU_DATE = (
    rf"{_WB_LEFT}\d{{1,2}}\.\d{{1,2}}\.\d{{2,4}}{_WB_RIGHT}|"
    rf"{_WB_LEFT}\d{{1,2}}\s+(?:{_RU_MONTHS})\s+\d{{4}}(?:\s*г\.?)?{_WB_RIGHT}"
)

# OGRN/OGRNIP: 13 or 15 digits — runs before INN(10/12) and passport.
_OGRN = rf"{_WB_LEFT}\d{{15}}{_WB_RIGHT}|{_WB_LEFT}\d{{13}}{_WB_RIGHT}"
_INN = rf"{_WB_LEFT}\d{{12}}{_WB_RIGHT}|{_WB_LEFT}\d{{10}}{_WB_RIGHT}"
_PASSPORT_RU = rf"{_WB_LEFT}\d{{4}}\s\d{{6}}{_WB_RIGHT}"
_SNILS = rf"{_WB_LEFT}\d{{3}}-\d{{3}}-\d{{3}}[\s\-]\d{{2}}{_WB_RIGHT}"

# Strict-mode bare numeric IDs: same shapes as OGRN/INN but require a
# Russian context keyword immediately before the number. Lookbehind has
# variable length so we approximate it with a pre-anchor capture group:
# match the keyword + separator first, then the digits, and remember only
# the digits range via a non-capturing group + lookbehind on the prefix.
# Python's `re` module does not allow variable-width lookbehind, so we
# instead match the whole `prefix + number` and rely on the post-pass
# code below to keep only the digit slice as the span.
_INN_STRICT = rf"({_ACCOUNT_CTX})({_WB_LEFT}\d{{12}}{_WB_RIGHT}|{_WB_LEFT}\d{{10}}{_WB_RIGHT})"
_OGRN_STRICT = rf"({_ACCOUNT_CTX})({_WB_LEFT}\d{{15}}{_WB_RIGHT}|{_WB_LEFT}\d{{13}}{_WB_RIGHT})"

# Generic secret-looking tokens (sk-..., AKIA..., GitHub-style PAT).
_SECRETS = (
    r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}\b|"
    r"\bAKIA[0-9A-Z]{16}\b|"
    r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}\b"
)


# Loose mode: the original behaviour — bare 10/12/13/15-digit IDs match.
_RULES_LOOSE: list[_RuleSet] = [
    _compile(Entity.ACCOUNT_NUMBER, _IBAN, _CREDIT_CARD),
    _compile(Entity.PRIVATE_PHONE, _RU_PHONE),
    _compile(Entity.PRIVATE_EMAIL, _EMAIL),
    _compile(Entity.PRIVATE_URL, _URL),
    _compile(Entity.PRIVATE_DATE, _RU_DATE, flags=re.IGNORECASE),
    _compile(Entity.ACCOUNT_NUMBER, _OGRN, _INN, _PASSPORT_RU, _SNILS),
    _compile(Entity.SECRET, _SECRETS),
]

# Strict mode: bare numeric IDs (`OGRN`, `INN`) only match with a context
# keyword. Passport/SNILS keep their formatting anchors so they're already
# unambiguous and stay outside the strict gate.
_RULES_STRICT: list[_RuleSet] = [
    _compile(Entity.ACCOUNT_NUMBER, _IBAN, _CREDIT_CARD),
    _compile(Entity.PRIVATE_PHONE, _RU_PHONE),
    _compile(Entity.PRIVATE_EMAIL, _EMAIL),
    _compile(Entity.PRIVATE_URL, _URL),
    _compile(Entity.PRIVATE_DATE, _RU_DATE, flags=re.IGNORECASE),
    _compile(Entity.ACCOUNT_NUMBER, _OGRN_STRICT, _INN_STRICT, _PASSPORT_RU, _SNILS),
    _compile(Entity.SECRET, _SECRETS),
]


def _spans_overlap(a: Span, b_start: int, b_end: int) -> bool:
    return not (a.end <= b_start or b_end <= a.start)


def _match_bounds(m: re.Match[str]) -> tuple[int, int]:
    """Return the meaningful span of the match.

    Strict-mode rules (`_INN_STRICT`, `_OGRN_STRICT`) capture the context
    keyword as one group and the digits as the next. When several strict
    alternatives are unioned together, the populated digits group is the
    last non-`None` capture in the match — that's what we slice out.
    """
    if m.groups():
        # Walk the captured groups right-to-left to find the digits range.
        for i in range(len(m.groups()), 0, -1):
            if m.group(i) is not None:
                return m.start(i), m.end(i)
    return m.start(), m.end()


def ru_postpass(
    text: str,
    spans: Iterable[Span],
    strict: bool = False,
) -> list[Span]:
    """Augment model spans with regex matches without introducing duplicates.

    See module docstring for `strict` semantics.
    """
    existing = list(spans)
    additions: list[Span] = []
    rules = _RULES_STRICT if strict else _RULES_LOOSE

    for rule in rules:
        for m in rule.pattern.finditer(text):
            start, end = _match_bounds(m)
            if any(_spans_overlap(s, start, end) for s in existing):
                continue
            if any(_spans_overlap(s, start, end) for s in additions):
                continue
            additions.append(
                Span(
                    entity=str(rule.entity),
                    text=text[start:end],
                    start=start,
                    end=end,
                    score=_REGEX_SCORE,
                )
            )

    merged = existing + additions
    merged.sort(key=lambda s: s.start)
    return merged
