"""Unit tests for the Russian regex post-pass.

These tests don't load the model — they only exercise the regex layer.
"""

from pf_tester.filter import Span
from pf_tester.ru_postpass import ru_postpass


def _entities(spans):
    return sorted({s.entity for s in spans})


def _by_entity(spans, entity):
    return [s.text for s in spans if s.entity == entity]


def test_postpass_catches_ru_phone_email():
    text = "Контакты: +7 (495) 123-45-67 или maria@mail.ru"
    spans = ru_postpass(text, [])
    assert "private_phone" in _entities(spans)
    assert "private_email" in _entities(spans)


def test_postpass_catches_passport_snils_inn():
    text = "Паспорт 4514 654321, СНИЛС 123-456-789 01, ИНН 770123456789."
    spans = ru_postpass(text, [])
    accounts = _by_entity(spans, "account_number")
    assert any("4514 654321" in a for a in accounts)
    assert any("123-456-789 01" in a for a in accounts)
    assert any("770123456789" in a for a in accounts)


def test_postpass_catches_credit_card_and_iban():
    text = "Карта 4111 1111 1111 1111, IBAN DE89 3704 0044 0532 0130 00"
    spans = ru_postpass(text, [])
    accounts = _by_entity(spans, "account_number")
    assert any("4111 1111 1111 1111" in a for a in accounts)
    assert any(a.startswith("DE89") for a in accounts)


def test_postpass_catches_ru_dates():
    text = "Выдан 12 января 2015 г., обновлён 03.06.2024."
    spans = ru_postpass(text, [])
    dates = _by_entity(spans, "private_date")
    assert any("12 января 2015" in d for d in dates)
    assert any("03.06.2024" in d for d in dates)


def test_postpass_catches_secret_tokens():
    text = "OPENAI_API_KEY=sk-proj-AbCdEfGhIjKlMnOpQrStUv and AKIAABCDEFGHIJKLMNOP"
    spans = ru_postpass(text, [])
    secrets = _by_entity(spans, "secret")
    assert any(s.startswith("sk-proj-") for s in secrets)
    assert any(s.startswith("AKIA") for s in secrets)


def test_postpass_does_not_duplicate_existing_spans():
    text = "Email me at alice@example.com"
    pre = [Span(entity="private_email", text="alice@example.com",
                start=12, end=29, score=0.99)]
    spans = ru_postpass(text, pre)
    emails = [s for s in spans if s.entity == "private_email"]
    assert len(emails) == 1
    assert emails[0].score == 0.99  # original kept, not a regex one


def test_postpass_catches_cyrillic_url():
    text = "Сайт компании: пример.рф/profile, документы на github.com/foo"
    spans = ru_postpass(text, [])
    urls = _by_entity(spans, "private_url")
    assert any("пример.рф" in u for u in urls)
    assert any("github.com" in u for u in urls)


def test_postpass_returns_sorted_spans():
    text = "Звоните +7 495 1234567, пишите на mail@example.ru"
    spans = ru_postpass(text, [])
    starts = [s.start for s in spans]
    assert starts == sorted(starts)


def test_postpass_skips_overlap_with_model():
    # Model already grabbed the email; passport regex must not be inserted
    # if the model already covers the same range.
    text = "Паспорт 4514 654321 выдан"
    pre = [Span(entity="account_number", text="4514 654321",
                start=8, end=19, score=0.95)]
    spans = ru_postpass(text, pre)
    accounts = [s for s in spans if s.entity == "account_number"]
    assert len(accounts) == 1


def test_postpass_catches_modern_tlds():
    text = "Visit demo.app, blog.dev, store.xyz, team.tech for details."
    spans = ru_postpass(text, [])
    urls = _by_entity(spans, "private_url")
    assert any("demo.app" in u for u in urls)
    assert any("blog.dev" in u for u in urls)
    assert any("store.xyz" in u for u in urls)
    assert any("team.tech" in u for u in urls)


def test_postpass_catches_cyrillic_modern_tlds():
    text = "Сайт сервис.рус и кабинет.онлайн"
    spans = ru_postpass(text, [])
    urls = _by_entity(spans, "private_url")
    assert any("сервис.рус" in u for u in urls)
    assert any("кабинет.онлайн" in u for u in urls)


def test_postpass_loose_mode_flags_bare_13_digit():
    # Default behaviour: a bare 13-digit blob is flagged as account_number,
    # which is the right call for free-form prose.
    text = "Reference 1234567890123 in the catalogue"
    spans = ru_postpass(text, [])
    assert any(s.entity == "account_number" for s in spans)


def test_postpass_strict_mode_skips_bare_13_digit():
    # Strict: same input, no context keyword -> no account_number span.
    text = "Reference 1234567890123 in the catalogue"
    spans = ru_postpass(text, [], strict=True)
    assert not any(s.entity == "account_number" for s in spans)


def test_postpass_strict_mode_keeps_inn_with_context():
    text = "ИНН 770123456789 — действителен."
    spans = ru_postpass(text, [], strict=True)
    accounts = _by_entity(spans, "account_number")
    # Span should be the digits only (the prefix is the regex capture group).
    assert "770123456789" in accounts


def test_postpass_strict_mode_passport_still_works():
    # Passport's "1234 567890" pattern is unique enough that strict mode
    # keeps detecting it without a keyword.
    text = "1234 567890 issued in 2014"
    spans = ru_postpass(text, [], strict=True)
    assert any(s.entity == "account_number" for s in spans)


def test_postpass_span_text_is_input_substring():
    text = "ИНН 770123456789."
    spans = ru_postpass(text, [], strict=True)
    for s in spans:
        assert s.text == text[s.start:s.end]
