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
