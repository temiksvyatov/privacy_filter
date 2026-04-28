"""FastAPI wrapper around the Privacy Filter for ad-hoc testing.

Run with:

    uvicorn pf_tester.service:app --reload
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .cache import SpanListCache, detect_cache_key
from .filter import DEFAULT_MODEL, PrivacyFilter, Span, get_filter
from .ru_postpass import ru_postpass
from .samples import SAMPLES

logger = logging.getLogger("pf_tester.service")

MODEL_NAME = os.getenv("PF_MODEL", DEFAULT_MODEL)
DEVICE = os.getenv("PF_DEVICE")  # e.g. "cpu", "cuda", "cuda:0"
DOMAIN = os.getenv("DOMAIN", "").strip()
CACHE_SIZE = int(os.getenv("PF_CACHE_SIZE", "256"))
MAX_UPLOAD_BYTES = int(os.getenv("PF_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
# Hard cap on JSON-body text to mirror the upload limit; rejected via 422.
MAX_TEXT_BYTES = int(os.getenv("PF_MAX_TEXT_BYTES", str(MAX_UPLOAD_BYTES)))
# Maximum concurrent inference jobs. Defaults to 2 — enough for one batch
# in flight while another readies on CPU. For GPU set to 1; for fat CPUs
# bump to physical-core-count via env.
INFERENCE_CONCURRENCY = max(1, int(os.getenv("PF_INFERENCE_CONCURRENCY", "2")))

# Process-wide readiness flag flipped by the lifespan handler. Liveness is
# always true once the process answers; readiness only flips on after the
# model is loaded so external orchestrators can route traffic correctly.
_READY = False

WEB_DIR = Path(__file__).parent / "web"


class DetectRequest(BaseModel):
    text: str = Field(..., description="Free-form text to scan.")
    min_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Drop spans with confidence below this threshold.",
    )
    ru_postpass: bool = Field(
        default=False,
        description="Run a Russian-targeted regex pass after the model "
                    "(passport / SNILS / INN / OGRN / RU phones, etc).",
    )
    ru_postpass_strict: bool = Field(
        default=False,
        description="When true, the regex pass requires a Russian context "
                    "keyword (ИНН/ОГРН/СНИЛС/паспорт) before bare numeric "
                    "account numbers. Cuts false positives on noisy input.",
    )

    @field_validator("text")
    @classmethod
    def _text_size(cls, v: str) -> str:
        # Pydantic max_length counts characters, not bytes; we want bytes so
        # the limit aligns with /redact/file's MAX_UPLOAD_BYTES (multibyte
        # cyrillic input pays its real cost). 422 from the validator.
        if len(v.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError(f"text exceeds {MAX_TEXT_BYTES} bytes")
        return v


class RedactRequest(DetectRequest):
    placeholder: str | None = Field(
        default=None,
        description="If set, every detected span is replaced with this string. "
                    "Otherwise spans are replaced with `[ENTITY_TYPE]`.",
    )
    mask_char: str | None = Field(
        default=None,
        min_length=1,
        max_length=1,
        description="Single character that repeats over the full span length "
                    "(e.g. '*' turns 'Alice' into '*****'). Takes precedence "
                    "over `placeholder`.",
    )


class SpanOut(BaseModel):
    entity: str
    text: str
    start: int
    end: int
    score: float


class DetectResponse(BaseModel):
    model: str
    spans: list[SpanOut]
    cached: bool = False


class RedactResponse(BaseModel):
    model: str
    redacted: str
    spans: list[SpanOut]
    cached: bool = False


# Detection cache: model spans keyed by (text, min_score, ru_postpass).
# Redaction is cheap and parameterised differently — it runs on every request
# against spans pulled from this cache. See pf_tester/cache.py for the lock.

_detect_cache: SpanListCache = SpanListCache(CACHE_SIZE)


def _detect_cached(
    pf: PrivacyFilter,
    text: str,
    min_score: float,
    ru_postpass_on: bool,
    ru_postpass_strict: bool = False,
) -> tuple[list[Span], bool]:
    key = detect_cache_key(text, min_score, ru_postpass_on, ru_postpass_strict)
    cached, hit = _detect_cache.get(key)
    if hit:
        return cached, True
    spans = pf.detect(text, min_score=min_score)
    if ru_postpass_on:
        spans = ru_postpass(text, spans, strict=ru_postpass_strict)
        if min_score > 0:
            spans = [s for s in spans if s.score >= min_score]
    _detect_cache.put(key, spans)
    return spans, False


_inference_semaphore: asyncio.Semaphore | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Pre-warm the model and set up bounded inference concurrency.

    A1/A3: any failure here used to bring the process down with a stack
    trace. We instead log the error and start the app in non-ready state
    so the readiness probe (and any orchestrator gating on it) refuses
    traffic. The liveness probe stays green because the process itself
    is healthy — restarting won't help if the model can't be downloaded.
    """
    global _READY, _inference_semaphore
    _inference_semaphore = asyncio.Semaphore(INFERENCE_CONCURRENCY)
    try:
        get_filter(MODEL_NAME, DEVICE)
        _READY = True
        logger.info(
            "model loaded: %s on %s (concurrency=%d)",
            MODEL_NAME, DEVICE or "auto", INFERENCE_CONCURRENCY,
        )
    except Exception:
        _READY = False
        # Log full traceback locally; details never leak to clients
        # (see _pf and the readiness endpoint).
        logger.exception("failed to load model %s", MODEL_NAME)
    try:
        yield
    finally:
        _READY = False


app = FastAPI(
    title="OpenAI Privacy Filter Tester",
    version="0.2.0",
    description="Minimal HTTP harness around openai/privacy-filter.",
    lifespan=_lifespan,
)

# Caddy terminates TLS and reverse-proxies to us, so requests reach the app
# either via `https://${DOMAIN}` or directly via `localhost:8000`. We allow
# both origins so the UI can be opened from either.
_origins = ["http://localhost:8000", "http://127.0.0.1:8000"]
if DOMAIN:
    _origins += [f"https://{DOMAIN}", f"http://{DOMAIN}"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def _pf() -> PrivacyFilter:
    """Fetch the singleton PrivacyFilter or surface a generic 503.

    The lifespan handler pre-loads the model, so in the happy path this
    just returns the cached instance. If lifespan failed, we return 503
    (service unavailable) without leaking the underlying exception
    message — HF errors can include disk paths, environment usernames
    and even tokens. Full details land in the structured log instead.
    """
    try:
        return get_filter(MODEL_NAME, DEVICE)
    except Exception:  # pragma: no cover - depends on env
        logger.exception("model load failed during request")
        raise HTTPException(
            status_code=503,
            detail="Privacy Filter model is not available; check server logs.",
        )


async def _detect_cached_async(
    pf: PrivacyFilter,
    text: str,
    min_score: float,
    ru_postpass_on: bool,
    ru_postpass_strict: bool,
) -> tuple[list[Span], bool]:
    """Run blocking detection in a worker thread under a bounded semaphore.

    A2: sync endpoints used to dump unbounded inference jobs into the
    default threadpool, where multiple CPU-bound calls would fight over
    the GIL and the pipeline's own threads. The semaphore caps in-flight
    inference at `PF_INFERENCE_CONCURRENCY` — cache hits still complete
    immediately because we check the cache before acquiring a slot.
    """
    key = detect_cache_key(text, min_score, ru_postpass_on, ru_postpass_strict)
    cached, hit = _detect_cache.get(key)
    if hit:
        return cached, True

    assert _inference_semaphore is not None  # set in lifespan
    async with _inference_semaphore:
        spans, hit = await asyncio.to_thread(
            _detect_cached, pf, text, min_score, ru_postpass_on, ru_postpass_strict
        )
    return spans, hit


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the bundled single-page UI."""
    index_path = WEB_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="UI bundle is missing")
    return FileResponse(index_path, media_type="text/html; charset=utf-8")


@app.get("/health")
def health() -> dict[str, object]:
    stats = _detect_cache.stats()
    return {
        "status": "ok" if _READY else "degraded",
        "ready": _READY,
        "model": MODEL_NAME,
        "domain": DOMAIN,
        "cache_size": stats["size"],
        "cache_capacity": stats["capacity"],
        "cache_hits": stats["hits"],
        "cache_misses": stats["misses"],
        "max_text_bytes": MAX_TEXT_BYTES,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "inference_concurrency": INFERENCE_CONCURRENCY,
    }


@app.get("/livez", include_in_schema=False)
def livez() -> dict[str, str]:
    """Liveness probe. The process answers, that's all this checks."""
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
def readyz() -> dict[str, str]:
    """Readiness probe. Flips green only after the model is loaded."""
    if not _READY:
        # 503 lets Kubernetes / Caddy keep this pod out of the rotation
        # while the model is still warming up or after a load failure.
        raise HTTPException(status_code=503, detail="model not ready")
    return {"status": "ok"}


@app.get("/samples")
def list_samples() -> dict[str, str]:
    return SAMPLES


@app.post("/detect", response_model=DetectResponse)
async def detect(req: DetectRequest) -> DetectResponse:
    pf = _pf()
    spans, cached = await _detect_cached_async(
        pf, req.text, req.min_score, req.ru_postpass, req.ru_postpass_strict
    )
    return DetectResponse(
        model=pf.model_name,
        spans=[SpanOut(**s.to_dict()) for s in spans],
        cached=cached,
    )


@app.post("/redact", response_model=RedactResponse)
async def redact(req: RedactRequest) -> RedactResponse:
    pf = _pf()
    spans, cached = await _detect_cached_async(
        pf, req.text, req.min_score, req.ru_postpass, req.ru_postpass_strict
    )
    try:
        redacted = pf.redact(
            req.text,
            placeholder=req.placeholder,
            spans=spans,
            mask_char=req.mask_char,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedactResponse(
        model=pf.model_name,
        redacted=redacted,
        spans=[SpanOut(**s.to_dict()) for s in spans],
        cached=cached,
    )


@app.post("/redact/file", response_model=RedactResponse)
async def redact_file(
    file: UploadFile = File(...),
    placeholder: str | None = Form(default=None),
    mask_char: str | None = Form(default=None),
    min_score: float = Form(default=0.0, ge=0.0, le=1.0),
    ru_postpass: bool = Form(default=False),
    ru_postpass_strict: bool = Form(default=False),
) -> RedactResponse:
    """Multipart variant for `curl -F file=@notes.txt …` style usage."""
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {MAX_UPLOAD_BYTES} bytes",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Not UTF-8: {exc}") from exc
    if mask_char is not None and len(mask_char) != 1:
        # Mirror the pydantic validator on RedactRequest.mask_char so the
        # multipart route reports the same 422 contract as the JSON route.
        raise HTTPException(status_code=422, detail="mask_char must be a single character")

    pf = _pf()
    spans, cached = await _detect_cached_async(
        pf, text, min_score, ru_postpass, ru_postpass_strict
    )
    try:
        redacted = pf.redact(text, placeholder=placeholder, spans=spans, mask_char=mask_char)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedactResponse(
        model=pf.model_name,
        redacted=redacted,
        spans=[SpanOut(**s.to_dict()) for s in spans],
        cached=cached,
    )
