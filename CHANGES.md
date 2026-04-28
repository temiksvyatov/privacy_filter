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
