"""Service smoke tests that monkeypatch the model so they run without GPU/network."""

import asyncio

from fastapi.testclient import TestClient

from pf_tester import filter as pf_filter
from pf_tester.filter import PrivacyFilter, Span
import pf_tester.service as svc


class _FakePF(PrivacyFilter):
    def __init__(self):
        self.model_name = "fake/privacy-filter"
        self.aggregation_strategy = "simple"
        self._pipe = None

    def detect(self, text, min_score=0.0):
        if "alice@example.com" in text:
            start = text.index("alice@example.com")
            score = 0.99
            if score < min_score:
                return []
            return [Span(
                entity="private_email",
                text="alice@example.com",
                start=start,
                end=start + len("alice@example.com"),
                score=score,
            )]
        return []


def _install_fake(monkeypatch):
    """Wire up a fake filter and pre-flip readiness/semaphore.

    The service relies on the lifespan handler to load the model and
    create the inference semaphore. TestClient skips lifespan unless
    used as a context manager — these tests instantiate it directly,
    so we set those globals manually.
    """
    fake = _FakePF()
    pf_filter.get_filter.cache_clear()
    svc._detect_cache.clear()
    monkeypatch.setattr(svc, "get_filter", lambda *a, **kw: fake)
    monkeypatch.setattr(svc, "_READY", True)
    monkeypatch.setattr(svc, "_inference_semaphore", asyncio.Semaphore(svc.INFERENCE_CONCURRENCY))


def test_health(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_detect_endpoint(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    r = client.post("/detect", json={"text": "ping alice@example.com please"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["spans"][0]["entity"] == "private_email"
    assert payload["spans"][0]["text"] == "alice@example.com"


def test_redact_endpoint(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    r = client.post(
        "/redact",
        json={"text": "ping alice@example.com please", "placeholder": "[X]"},
    )
    assert r.status_code == 200
    assert r.json()["redacted"] == "ping [X] please"


def test_samples_endpoint(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    r = client.get("/samples")
    assert r.status_code == 200
    body = r.json()
    assert "person_address_date" in body


def test_index_serves_html(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "OpenAI Privacy Filter" in r.text


def test_static_assets(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]


def test_redact_mask_char_via_api(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    r = client.post(
        "/redact",
        json={"text": "ping alice@example.com please", "mask_char": "*"},
    )
    assert r.status_code == 200
    assert r.json()["redacted"] == "ping ***************** please"


def test_redact_file_endpoint(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    payload = "ping alice@example.com please".encode("utf-8")
    r = client.post(
        "/redact/file",
        files={"file": ("notes.txt", payload, "text/plain")},
        data={"mask_char": "*"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["redacted"] == "ping ***************** please"
    assert body["spans"][0]["entity"] == "private_email"


def test_redact_file_rejects_too_large(monkeypatch):
    _install_fake(monkeypatch)
    monkeypatch.setattr(svc, "MAX_UPLOAD_BYTES", 16)
    client = TestClient(svc.app)
    big = ("x" * 64).encode("utf-8")
    r = client.post(
        "/redact/file",
        files={"file": ("big.txt", big, "text/plain")},
    )
    assert r.status_code == 413


def test_detect_cache_marks_second_hit(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    body = {"text": "ping alice@example.com please"}
    r1 = client.post("/detect", json=body)
    r2 = client.post("/detect", json=body)
    assert r1.json()["cached"] is False
    assert r2.json()["cached"] is True


def test_min_score_filters(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    r = client.post(
        "/detect",
        json={"text": "ping alice@example.com please", "min_score": 0.999999},
    )
    assert r.status_code == 200
    assert r.json()["spans"] == []


def test_detect_rejects_oversized_text(monkeypatch):
    _install_fake(monkeypatch)
    monkeypatch.setattr(svc, "MAX_TEXT_BYTES", 16)
    client = TestClient(svc.app)
    r = client.post("/detect", json={"text": "x" * 64})
    assert r.status_code == 422


def test_redact_mask_char_too_long_returns_422(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    r = client.post(
        "/redact",
        json={"text": "ping alice@example.com please", "mask_char": "**"},
    )
    assert r.status_code == 422


def test_redact_file_mask_char_too_long_returns_422(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    payload = "ping alice@example.com please".encode("utf-8")
    r = client.post(
        "/redact/file",
        files={"file": ("notes.txt", payload, "text/plain")},
        data={"mask_char": "**"},
    )
    assert r.status_code == 422


def test_postpass_min_score_filters_regex_hits(monkeypatch):
    # F5: regex hits get a fixed score (~0.95). When the user asks for a
    # higher threshold the service must drop them too.
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    body = {
        "text": "ИНН 770123456789",
        "ru_postpass": True,
        "min_score": 0.99,
    }
    r = client.post("/detect", json=body)
    assert r.status_code == 200
    assert r.json()["spans"] == []


def test_postpass_strict_skips_bare_numbers_via_api(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    body = {
        "text": "Reference 1234567890123 in the catalogue",
        "ru_postpass": True,
        "ru_postpass_strict": True,
    }
    r = client.post("/detect", json=body)
    assert r.status_code == 200
    assert r.json()["spans"] == []


def test_livez_always_ok(monkeypatch):
    monkeypatch.setattr(svc, "_READY", False)
    client = TestClient(svc.app)
    r = client.get("/livez")
    assert r.status_code == 200


def test_readyz_503_when_not_ready(monkeypatch):
    monkeypatch.setattr(svc, "_READY", False)
    client = TestClient(svc.app)
    r = client.get("/readyz")
    assert r.status_code == 503


def test_readyz_200_when_ready(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    r = client.get("/readyz")
    assert r.status_code == 200


def test_health_reports_limits(monkeypatch):
    _install_fake(monkeypatch)
    client = TestClient(svc.app)
    body = client.get("/health").json()
    assert "max_text_bytes" in body
    assert "max_upload_bytes" in body
    assert body["inference_concurrency"] >= 1


def test_pf_unavailable_returns_503(monkeypatch):
    pf_filter.get_filter.cache_clear()
    svc._detect_cache.clear()

    def boom(*a, **kw):
        raise RuntimeError("HF token expired (would-be-secret)")

    monkeypatch.setattr(svc, "get_filter", boom)
    monkeypatch.setattr(svc, "_READY", True)
    monkeypatch.setattr(svc, "_inference_semaphore", asyncio.Semaphore(1))
    client = TestClient(svc.app)
    r = client.post("/detect", json={"text": "anything"})
    assert r.status_code == 503
    # Make sure the inner error message did not leak to the client.
    assert "HF token" not in r.text
    assert "would-be-secret" not in r.text
