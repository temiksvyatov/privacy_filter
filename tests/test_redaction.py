"""Pure unit tests for the redaction helper that don't load the real model."""

from pf_tester.filter import Entity, PrivacyFilter, Span, redact


class _Stub(PrivacyFilter):
    def __init__(self):  # bypass model loading
        self.model_name = "stub"
        self.aggregation_strategy = "simple"
        self._pipe = None


def test_redact_replaces_spans_with_entity_tag():
    pf = _Stub()
    text = "Alice lives in Berlin."
    spans = [
        Span(entity="private_person", text="Alice", start=0, end=5, score=0.99),
        Span(entity="private_address", text="Berlin", start=15, end=21, score=0.98),
    ]
    assert pf.redact(text, spans=spans) == "[PRIVATE_PERSON] lives in [PRIVATE_ADDRESS]."


def test_redact_with_custom_placeholder():
    pf = _Stub()
    text = "Email me at a@b.com."
    spans = [Span(entity="private_email", text="a@b.com", start=12, end=19, score=0.99)]
    assert pf.redact(text, placeholder="[REDACTED]", spans=spans) == "Email me at [REDACTED]."


def test_redact_no_spans_returns_original():
    pf = _Stub()
    assert pf.redact("nothing sensitive here", spans=[]) == "nothing sensitive here"


def test_redact_skips_overlapping_spans():
    pf = _Stub()
    text = "abcdef"
    spans = [
        Span(entity="x", text="abc", start=0, end=3, score=0.9),
        Span(entity="y", text="bcd", start=1, end=4, score=0.9),  # overlaps, dropped
        Span(entity="z", text="ef", start=4, end=6, score=0.9),
    ]
    assert pf.redact(text, spans=spans) == "[X]d[Z]"


def test_redact_mask_char_preserves_length():
    pf = _Stub()
    text = "Иванов lives in Berlin."
    spans = [
        Span(entity="private_person", text="Иванов", start=0, end=6, score=0.99),
        Span(entity="private_address", text="Berlin", start=16, end=22, score=0.98),
    ]
    assert pf.redact(text, spans=spans, mask_char="*") == "****** lives in ******."


def test_redact_mask_char_wins_over_placeholder():
    pf = _Stub()
    text = "abc"
    spans = [Span(entity="x", text="abc", start=0, end=3, score=0.9)]
    assert pf.redact(text, spans=spans, placeholder="[Z]", mask_char="*") == "***"


def test_redact_mask_char_must_be_single_char():
    pf = _Stub()
    import pytest
    with pytest.raises(ValueError):
        pf.redact("abc", spans=[Span("x", "abc", 0, 3, 0.9)], mask_char="**")


def test_module_level_redact_does_not_need_filter():
    text = "hi Alice"
    spans = [Span(entity="private_person", text="Alice", start=3, end=8, score=0.9)]
    assert redact(text, spans, placeholder="[X]") == "hi [X]"


def test_module_level_redact_default_tag():
    text = "hi Alice"
    spans = [Span(entity=Entity.PRIVATE_PERSON, text="Alice", start=3, end=8, score=0.9)]
    assert redact(text, spans) == "hi [PRIVATE_PERSON]"


def test_entity_enum_values_match_strings():
    assert Entity.PRIVATE_EMAIL == "private_email"
    assert str(Entity.SECRET) == "secret"
