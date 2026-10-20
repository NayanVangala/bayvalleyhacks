"""Stdlib fallback server for Lexia Read Mode.

Implements the same 5 routes as lexia.api.app using only http.server, for
environments where FastAPI / uvicorn are not installed:

  GET  /health
  POST /read                      (JSON body only: {"image_base64": str})
  GET  /read/{session_id}
  POST /read/{session_id}/speak
  GET  /audio/{filename}

Uses the same sibling contracts as the FastAPI app:
  lexia.ocr, lexia.reading, lexia.tts.
"""

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from lexia.ocr import OCRUnavailable, get_engine
from lexia.reading import (
    RuleBasedSimplifier,
    SessionStore,
    chunk_text,
    clean_text,
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
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

engine = get_engine()
tts_engine = get_tts_engine()
store = SessionStore()

HOST = "0.0.0.0"
PORT = 8100


def _get(obj: Any, *names: str, default: Any = None) -> Any:
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
            text, index, word_count = chunk, i, len(chunk.split())
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


def _simplify_rule_based(text: str) -> str:
    candidate: Any = RuleBasedSimplifier
    try:
        instance = candidate()
    except TypeError:
        instance = candidate
    simplify_method = getattr(instance, "simplify", None)
    if callable(simplify_method):
        return simplify_method(text)
    if callable(instance):
        return instance(text)
    return str(candidate(text))


def _create_session(raw_text, cleaned, chunks, simplified, engine_name, mean_conf):
    try:
        return store.create(
            raw_text, cleaned, chunks, simplified, engine_name, mean_conf
        )
    except TypeError:
        return store.create(
            raw_text=raw_text,
            cleaned_text=cleaned,
            chunks=chunks,
            simplified=simplified,
            ocr_engine=engine_name,
            mean_confidence=mean_conf,
        )


def _session_payload(session: Any) -> dict[str, Any]:
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

    return {
        "session_id": pick("id", "session_id", default=""),
        "text": pick("cleaned_text", "cleaned", "text", default=""),
        "raw_text": pick("raw_text", "raw", default=""),
        "chunks": _chunk_payload(pick("chunks", default=[])),
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


class Handler(BaseHTTPRequestHandler):
    server_version = "LexiaStdlib/1.0"

    # -- plumbing -----------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # keep stdout clean
        pass

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    # -- routes -------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "ocr_engine": engine.name,
                    "tts_engine": tts_engine.name,
                },
            )
            return
        if path.startswith("/read/"):
            session_id = unquote(path[len("/read/"):]).strip("/")
            session = store.get(session_id) if session_id else None
            if session is None:
                self._send_json(404, {"detail": "session not found"})
                return
            self._send_json(200, _session_payload(session))
            return
        if path.startswith("/audio/"):
            filename = unquote(path[len("/audio/"):])
            if filename != Path(filename).name or filename in {"", ".", ".."}:
                self._send_json(404, {"detail": "audio not found"})
                return
            file_path = AUDIO_DIR / filename
            if not file_path.is_file():
                self._send_json(404, {"detail": "audio not found"})
                return
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                mimetypes.guess_type(filename)[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._send_json(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        path = urlparse(self.path).path
        if path == "/read":
            self._handle_read()
            return
        if path.startswith("/read/") and path.endswith("/speak"):
            session_id = unquote(path[len("/read/"):-len("/speak")]).strip("/")
            self._handle_speak(session_id)
            return
        self._send_json(404, {"detail": "not found"})

    # -- handlers -----------------------------------------------------------
    def _handle_read(self) -> None:
        body = self._read_json()
        encoded = body.get("image_base64") or body.get("image") or ""
        image_bytes = _decode_base64(encoded) if isinstance(encoded, str) else b""
        if not image_bytes:
            self._send_json(400, {"detail": "image is required"})
            return
        try:
            result = engine.extract(image_bytes)
        except OCRUnavailable as exc:
            self._send_json(503, {"detail": str(exc) or "OCR unavailable"})
            return
        raw_text = _get(result, "text", default="") or ""
        mean_confidence = _get(result, "mean_confidence", "confidence", default=0.0)
        cleaned = clean_text(raw_text)
        simplified = False
        if body.get("simplify"):
            cleaned = _simplify_rule_based(cleaned)
            simplified = True
        chunks = chunk_text(cleaned)
        session = _create_session(
            raw_text, cleaned, chunks, simplified, engine.name, mean_confidence
        )
        self._send_json(
            200,
            {
                "session_id": _get(session, "id", "session_id", default=""),
                "text": cleaned,
                "raw_text": raw_text,
                "chunks": _chunk_payload(chunks),
                "mean_confidence": mean_confidence,
                "engine": engine.name,
            },
        )

    def _handle_speak(self, session_id: str) -> None:
        session = store.get(session_id) if session_id else None
        if session is None:
            self._send_json(404, {"detail": "session not found"})
            return
        text = _get(session, "cleaned_text", "cleaned", "text", default="") or ""
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        try:
            result = tts_engine.synthesize(text, AUDIO_DIR)
        except TTSUnavailable as exc:
            self._send_json(503, {"detail": str(exc) or "TTS unavailable"})
            return
        audio_path = _get(result, "audio_path", "path", default="")
        try:
            store.set_audio(session_id, audio_path)
        except TypeError:
            store.set_audio(session_id, str(audio_path))
        self._send_json(
            200,
            {
                "audio_url": f"/audio/{Path(str(audio_path)).name}",
                "duration_seconds": _get(
                    result, "duration_seconds", "duration", default=0.0
                ),
                "engine": tts_engine.name,
            },
        )


def serve(host: str = HOST, port: int = PORT) -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Lexia Read Mode (stdlib) listening on {host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
