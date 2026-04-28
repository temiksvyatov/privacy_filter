"""FastAPI wrapper around the Privacy Filter for ad-hoc testing.

Run with:

    uvicorn pf_tester.service:app --reload
"""

from __future__ import annotations

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

MODEL_NAME = os.getenv("PF_MODEL", DEFAULT_MODEL)
DEVICE = os.getenv("PF_DEVICE")  # e.g. "cpu", "cuda", "cuda:0"
DOMAIN = os.getenv("DOMAIN", "").strip()
CACHE_SIZE = int(os.getenv("PF_CACHE_SIZE", "256"))
MAX_UPLOAD_BYTES = int(os.getenv("PF_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
# Hard cap on JSON-body text to mirror the upload limit; rejected via 422.
MAX_TEXT_BYTES = int(os.getenv("PF_MAX_TEXT_BYTES", str(MAX_UPLOAD_BYTES)))

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
    pf: PrivacyFilter, text: str, min_score: float, ru_postpass_on: bool
) -> tuple[list[Span], bool]:
    key = detect_cache_key(text, min_score, ru_postpass_on)
    cached, hit = _detect_cache.get(key)
    if hit:
        return cached, True
    spans = pf.detect(text, min_score=min_score)
    if ru_postpass_on:
        spans = ru_postpass(text, spans)
    _detect_cache.put(key, spans)
    return spans, False


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Pre-warm the model so the first request isn't slow.
    get_filter(MODEL_NAME, DEVICE)
    yield


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
    try:
        return get_filter(MODEL_NAME, DEVICE)
    except Exception as exc:  # pragma: no cover - depends on env
        raise HTTPException(status_code=500, detail=f"Failed to load model: {exc}") from exc


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
        "status": "ok",
        "model": MODEL_NAME,
        "domain": DOMAIN,
        "cache_size": stats["size"],
        "cache_capacity": stats["capacity"],
        "cache_hits": stats["hits"],
        "cache_misses": stats["misses"],
    }


@app.get("/samples")
def list_samples() -> dict[str, str]:
    return SAMPLES


@app.post("/detect", response_model=DetectResponse)
def detect(req: DetectRequest) -> DetectResponse:
    pf = _pf()
    spans, cached = _detect_cached(pf, req.text, req.min_score, req.ru_postpass)
    return DetectResponse(
        model=pf.model_name,
        spans=[SpanOut(**s.to_dict()) for s in spans],
        cached=cached,
    )


@app.post("/redact", response_model=RedactResponse)
def redact(req: RedactRequest) -> RedactResponse:
    pf = _pf()
    spans, cached = _detect_cached(pf, req.text, req.min_score, req.ru_postpass)
    try:
        redacted = pf.redact(
            req.text,
            placeholder=req.placeholder,
            spans=spans,
            mask_char=req.mask_char,
        )
    except ValueError as exc:
        # PrivacyFilter.redact raises ValueError on bad mask_char; surface as
        # 422 to keep validation contract uniform across routes.
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
    spans, cached = _detect_cached(pf, text, min_score, ru_postpass)
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
