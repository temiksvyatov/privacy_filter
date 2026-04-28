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
