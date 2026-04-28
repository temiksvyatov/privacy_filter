# Refactor Changelog

Track of fixes/improvements applied during refactor of `privacy_filter`,
guided by `REVIEW.md`. One commit per logical group.

Started: 2026-04-28.

Legend: refers to `REVIEW.md` items (e.g. `F4`, `A1`, `P6`).

---

## Commits

### 1. `fix(filter): slice span.text from input, not pipeline word`

- **F4** (REVIEW §3.2): `Span.text` was set from `item["word"]`, which under
  some tokenizers (BPE/WordPiece) returns sub-token markers (e.g. `##`) or
  leading whitespace. Switched to `text[start:end]` so spans always equal an
  exact substring of the input — required for correct redaction and UI
  highlighting.
- **Q1** (REVIEW §5): the 20-line span-conversion loop was duplicated
  between `detect()` and `detect_batch()`. Extracted into a single
  `_raw_to_spans(text, raw, min_score)` static helper used by both.

Touched: `pf_tester/filter.py`. Tests: 27/27 green.

### 2. `feat(api): cap text size and unify mask_char validation`

- **P6** (REVIEW §2.2): JSON `/detect` and `/redact` accepted arbitrarily
  large bodies — only `/redact/file` had a 5 MB cap. Added a
  `field_validator` on `DetectRequest.text` that rejects payloads larger
  than `PF_MAX_TEXT_BYTES` (defaults to `PF_MAX_UPLOAD_BYTES`). Counts
  bytes, not characters, so multibyte cyrillic input is measured fairly.
  Returns 422 from pydantic before the model is touched.
- **F3** (REVIEW §3.2): `mask_char` validation lived in three places
  (pydantic 422, manual 400 in `/redact/file`, `ValueError` 500 from
  `redact()`). Now all three converge on **422**: the multipart route
  raises `HTTPException(422, …)` and both `/redact` and `/redact/file`
  catch `ValueError` from `PrivacyFilter.redact` and re-raise as 422.
- Tightened: `min_score` Form field now also has `ge=0.0, le=1.0` to match
  the pydantic body model.

Tests: added 3 cases (`oversized_text`, `mask_char_too_long` ×2). 30/30 green.

### 3. `feat(cache): thread-safe LRU module + faster blake2b key`

- **A1** (REVIEW §1.2): the detection cache was a bare `OrderedDict`
  mutated from sync endpoints in FastAPI's threadpool. Concurrent
  `move_to_end` / `popitem` would intermittently raise
  `RuntimeError: dictionary changed size during iteration` under load.
  New `pf_tester/cache.py` wraps an `OrderedDict` in a `threading.Lock`
  and exposes `get` / `put` / `clear` / `stats`.
- **A8** (REVIEW §1.2): cache logic no longer lives in the HTTP module.
  `service.py` now imports `SpanListCache` and `detect_cache_key` from a
  dedicated module, making a future Redis swap an interface change.
- **P1** (REVIEW §2.2): hashed key switched from SHA-256 over a JSON
  blob to `blake2b(digest_size=16)` — ~2–3× faster on CPython for the
  long-text case.
- **P2** (REVIEW §2.2): replaced `json.dumps({...})` with a tagged
  concatenation (`text \x1f score \x1f flag`) — cheaper and immune to
  json-escape edge cases. Tagged with ASCII Unit Separator to avoid
  field-boundary collisions.
- `/health` now reports `cache_hits` / `cache_misses` so operators can
  see hit-rate without `/metrics` (still on the roadmap, **P9**).

Tests: added `tests/test_cache.py` with 9 cases covering LRU semantics,
hit/miss accounting, capacity validation, concurrent writers (8 threads
× 2000 ops) and key derivation properties. 39/39 green.

### 4. `refactor(filter): module-level redact + Entity enum`

- **A6** (REVIEW §1.2): introduced `class Entity(StrEnum)` with the eight
  PII categories the model emits. Single source of truth for taxonomy
  references in CLI / service / regex postpass / tests. Adding a 9th
  category is now a one-line change instead of touching 4 files.
- **Q2** (REVIEW §5): `_RedactOnly` was a hack — a stand-in object that
  invoked `PrivacyFilter.redact(self, …)` as an unbound method to get
  redaction without loading the model. Worked because `redact()` didn't
  touch `self`, but it was fragile (any future `self.something` would
  break it silently). Extracted the algorithm into a module-level
  `redact(text, spans, placeholder, mask_char)` function. The class
  method is now a one-line delegator that auto-runs `detect()` if spans
  aren't provided. CLI imports the module function directly and the
  hack is gone.

Tests: added 3 cases for the module function and the enum string-equality
contract. 42/42 green.
