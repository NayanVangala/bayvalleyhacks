"""FastAPI app for Lexia Read Mode.

Routes:
  GET  /health
  POST /read                        (multipart file "image" OR JSON base64)
  GET  /read/{session_id}
  POST /read/{session_id}/speak
  GET  /audio/{filename}

Sibling contracts (owned by other workers, imported here, not edited):
  lexia.ocr:     get_engine, OCRUnavailable
  lexia.reading: clean_text, chunk_text, get_simplifier, RuleBasedSimplifier,
                 SessionStore, to_dict
  lexia.tts:     get_tts_engine, TTSUnavailable
"""

from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from lexia.ocr import OCRUnavailable, get_engine
from lexia.reading import (
    RuleBasedSimplifier,
    SessionStore,
    chunk_text,
    clean_text,
    get_simplifier,
    to_dict,
)
from lexia.tts import TTSUnavailable, get_tts_engine

DATA_DIR = Path(
    os.environ.get(
        "LEXIA_DATA_DIR",
        Path(__file__).resolve().parent.parent / "data",
    )
)
AUDIO_DIR = DATA_DIR / "audio"

engine = get_engine()
tts_engine = get_tts_engine()
store = SessionStore()

app = FastAPI(title="Lexia Read Mode")


@app.on_event("startup")
def _ensure_dirs() -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)


# Also create at import time so tests and direct calls work without startup.
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """Read the first matching attribute or dict key from obj."""
    for name in names:
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                return obj[name]
        elif hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _chunk_payload(chunks: Any) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks or []):
        if isinstance(chunk, str):
            text = chunk
            index = i
            word_count = len(text.split())
        else:
            text = _get(chunk, "text", default="")
            index = _get(chunk, "index", default=i)
            word_count = _get(chunk, "word_count", default=None)
            if word_count is None:
                word_count = len(str(text).split())
        payload.append(
            {"index": int(index), "text": str(text), "word_count": int(word_count)}
        )
    return payload


def _apply_simplifier(simplifier: Any, text: str) -> str:
    """Simplifiers may be callables or objects with a simplify() method."""
    simplify_method = getattr(simplifier, "simplify", None)
    if callable(simplify_method):
        return simplify_method(text)
    if callable(simplifier):
        return simplifier(text)
    raise TypeError("simplifier is not callable and has no simplify() method")


def _simplify_rule_based(text: str) -> str:
    """Force the rule based simplifier, regardless of environment config."""
    candidate: Any = RuleBasedSimplifier
    # Class: instantiate first, then apply. Plain function: call directly.
    try:
        instance = candidate()  # type: ignore[operator]
    except TypeError:
        instance = candidate
    try:
        return _apply_simplifier(instance, text)
    except TypeError:
        # The callable itself was the simplifier function.
        return str(candidate(text))  # type: ignore[operator]


def _create_session(
    raw_text: str,
    cleaned: str,
    chunks: Any,
    simplified: bool,
    engine_name: str,
    mean_confidence: Any,
):
    """Call store.create, tolerating positional or keyword style signatures."""
    try:
        return store.create(
            raw_text, cleaned, chunks, simplified, engine_name, mean_confidence
        )
    except TypeError:
        pass
    kwargs_variants = [
        {
            "raw_text": raw_text,
            "cleaned_text": cleaned,
            "chunks": chunks,
            "simplified": simplified,
            "ocr_engine": engine_name,
            "mean_confidence": mean_confidence,
        },
        {
            "raw_text": raw_text,
            "cleaned": cleaned,
            "chunks": chunks,
            "simplified": simplified,
            "engine": engine_name,
            "mean_confidence": mean_confidence,
        },
        {"raw_text": raw_text, "cleaned_text": cleaned, "chunks": chunks},
        {"raw_text": raw_text, "cleaned_text": cleaned},
    ]
    last_error: Exception | None = None
    for kwargs in kwargs_variants:
        try:
            return store.create(**kwargs)
        except TypeError as exc:  # signature mismatch, try the next shape
            last_error = exc
    raise RuntimeError(f"SessionStore.create signature not recognised: {last_error}")


def _session_payload(session: Any) -> dict[str, Any]:
    """Build the public payload for a stored session."""
    data: dict[str, Any] = {}
    try:
        converted = to_dict(session)
        if isinstance(converted, dict):
            data = converted
    except Exception:
        data = {}

    def pick(*names: str, default: Any = None) -> Any:
        value = _get(session, *names, default=None)
        if value is not None:
            return value
        for name in names:
            if name in data and data[name] is not None:
                return data[name]
        return default

    cleaned = pick("cleaned_text", "cleaned", "text", default="")
    raw_text = pick("raw_text", "raw", default="")
    chunks = pick("chunks", default=[])
    return {
        "session_id": pick("id", "session_id", default=""),
        "text": cleaned,
        "raw_text": raw_text,
        "chunks": _chunk_payload(chunks),
        "mean_confidence": pick("mean_confidence", "confidence", default=0.0),
        "engine": pick("ocr_engine", "engine", default=engine.name),
    }


def _decode_base64(value: str) -> bytes:
    text = value.strip()
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    text = "".join(text.split())
    try:
        return base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError):
        return b""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "ocr_engine": engine.name, "tts_engine": tts_engine.name}


@app.post("/read")
async def read_image(request: Request) -> dict[str, Any]:
    image_bytes: bytes = b""
    simplify = False

    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        form = await request.form()
        simplify = _truthy(form.get("simplify", False))
        upload = form.get("image")
        if upload is not None:
            if hasattr(upload, "read"):
                image_bytes = await upload.read()  # UploadFile
            elif isinstance(upload, (bytes, bytearray)):
                image_bytes = bytes(upload)
            else:
                image_bytes = str(upload).encode()
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            simplify = _truthy(body.get("simplify", False))
            encoded = body.get("image_base64") or body.get("image") or ""
            if isinstance(encoded, str) and encoded:
                image_bytes = _decode_base64(encoded)
            elif isinstance(encoded, (bytes, bytearray)):
                image_bytes = bytes(encoded)

    if not image_bytes:
        raise HTTPException(status_code=400, detail="image is required")

    try:
        result = engine.extract(image_bytes)
    except OCRUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc) or "OCR unavailable")
    except Exception as exc:
        # Engines that surface availability failures under other exception
        # types still map to 503 if the type name says so.
        if exc.__class__.__name__ == "OCRUnavailable":
            raise HTTPException(status_code=503, detail=str(exc) or "OCR unavailable")
        raise

    raw_text = _get(result, "text", default="") or ""
    mean_confidence = _get(result, "mean_confidence", "confidence", default=0.0)

    cleaned = clean_text(raw_text)
    simplified = False
    if simplify:
        cleaned = _simplify_rule_based(cleaned)
        simplified = True

    chunks = chunk_text(cleaned)
    session = _create_session(
        raw_text, cleaned, chunks, simplified, engine.name, mean_confidence
    )

    return {
        "session_id": _get(session, "id", "session_id", default=""),
        "text": cleaned,
        "raw_text": raw_text,
        "chunks": _chunk_payload(chunks),
        "mean_confidence": mean_confidence,
        "engine": engine.name,
    }


@app.get("/read/{session_id}")
def get_read(session_id: str) -> dict[str, Any]:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return _session_payload(session)


@app.post("/read/{session_id}/speak")
def speak(session_id: str) -> dict[str, Any]:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    text = _get(session, "cleaned_text", "cleaned", "text", default="") or ""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    try:
        result = tts_engine.synthesize(text, AUDIO_DIR)
    except TTSUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc) or "TTS unavailable")
    except Exception as exc:
        if exc.__class__.__name__ == "TTSUnavailable":
            raise HTTPException(status_code=503, detail=str(exc) or "TTS unavailable")
        raise

    audio_path = _get(result, "audio_path", "path", default="")
    try:
        store.set_audio(session_id, audio_path)
    except TypeError:
        store.set_audio(session_id, str(audio_path))

    filename = Path(str(audio_path)).name
    return {
        "audio_url": f"/audio/{filename}",
        "duration_seconds": _get(result, "duration_seconds", "duration", default=0.0),
        "engine": tts_engine.name,
    }


@app.get("/audio/{filename}")
def get_audio(filename: str):
    if filename != Path(filename).name or filename in {"", ".", ".."}:
        raise HTTPException(status_code=404, detail="audio not found")
    path = AUDIO_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="audio not found")
    return FileResponse(path)
