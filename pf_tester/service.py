"""FastAPI wrapper around the Privacy Filter for ad-hoc testing.

Run with:

    uvicorn pf_tester.service:app --reload
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .filter import DEFAULT_MODEL, PrivacyFilter, get_filter
from .samples import SAMPLES

MODEL_NAME = os.getenv("PF_MODEL", DEFAULT_MODEL)
DEVICE = os.getenv("PF_DEVICE")  # e.g. "cpu", "cuda", "cuda:0"
DOMAIN = os.getenv("DOMAIN", "").strip()

WEB_DIR = Path(__file__).parent / "web"


class DetectRequest(BaseModel):
    text: str = Field(..., description="Free-form text to scan.")


class RedactRequest(BaseModel):
    text: str
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


class RedactResponse(BaseModel):
    model: str
    redacted: str
    spans: list[SpanOut]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Pre-warm the model so the first request isn't slow.
    get_filter(MODEL_NAME, DEVICE)
    yield


app = FastAPI(
    title="OpenAI Privacy Filter Tester",
    version="0.1.0",
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
def health() -> dict[str, str]:
    return {"status": "ok", "model": MODEL_NAME, "domain": DOMAIN}


@app.get("/samples")
def list_samples() -> dict[str, str]:
    return SAMPLES


@app.post("/detect", response_model=DetectResponse)
def detect(req: DetectRequest) -> DetectResponse:
    pf = _pf()
    spans = pf.detect(req.text)
    return DetectResponse(
        model=pf.model_name,
        spans=[SpanOut(**s.to_dict()) for s in spans],
    )


@app.post("/redact", response_model=RedactResponse)
def redact(req: RedactRequest) -> RedactResponse:
    pf = _pf()
    spans = pf.detect(req.text)
    redacted = pf.redact(
        req.text,
        placeholder=req.placeholder,
        spans=spans,
        mask_char=req.mask_char,
    )
    return RedactResponse(
        model=pf.model_name,
        redacted=redacted,
        spans=[SpanOut(**s.to_dict()) for s in spans],
    )
