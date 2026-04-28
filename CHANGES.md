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

### 5. `feat(postpass): strict mode + modern TLDs + union-regex`

- **F1** (REVIEW §3.2): bare 10/12/13/15-digit numbers were unconditionally
  flagged as `account_number`, which produces false positives on logs,
  catalogues and barcodes. Added a **strict mode** (`strict=True` /
  `--ru-postpass-strict` / `ru_postpass_strict: true`) that requires a
  Russian context keyword (ИНН/ОГРН/ОГРНИП/СНИЛС/паспорт/INN/OGRN/…)
  before the digits. Loose mode (default) is unchanged so existing
  callers see no regression. The strict regex captures the keyword in
  group 1 and the digits in group 2; `_match_bounds` slices out only
  the digit range as the span, so `Span.text` matches the input.
- **F2** (REVIEW §3.2): expanded the bare-host TLD allow-list. Added
  modern gTLDs (`.app`, `.dev`, `.ai`, `.xyz`, `.tech`, `.cloud`,
  `.site`, `.store`, `.page`, `.co`, `.me`, `.edu`, `.gov`), the
  Cyrillic `.рус` and `.онлайн`, and CIS ccTLDs (`.by`, `.kz`, `.ua`,
  `.uk`).
- **P7** (REVIEW §2.2): consolidated the rule list. Each entity now
  compiles into one alternation of patterns (`re.compile("|".join(...))`),
  giving us one `re.finditer` pass per entity instead of one per
  pattern. Same overlap semantics, fewer regex objects to walk.
- **F5** (REVIEW §3.2): the CLI's `--min-score` was only applied inside
  `pf.detect()`; regex hits skipped the gate, so a user asking for
  `--min-score 0.99` unexpectedly got 0.95-scored regex spans. Drop
  spans below `min_score` after the postpass too. The same fix lives in
  the service's `_detect_cached` so all entry points behave the same.
- Span text now equals an input substring even for strict-mode hits
  (small but visible improvement for UI highlighting).

Tests: added 8 cases (modern TLDs, Cyrillic TLDs, loose vs strict
behaviour for bare numbers, strict-mode INN with context, span-text
substring contract, service-level F5 + strict propagation). 51/51 green.
