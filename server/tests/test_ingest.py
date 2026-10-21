"""Integration tests for the OnRecord HTTP and WebSocket surface.

The app is built with create_app using injected fakes: a real Store on a
temporary DATA_DIR, FakeVectorStore, FakeTranscriber (default script),
FakeFaceEmbedder, FakeVoiceEmbedder, and HashingTextEmbedder. No network,
no ML models, and no real audio or photos are involved.

Run from the server directory:

    .venv/bin/python -m pytest tests/test_ingest.py -q
"""

from __future__ import annotations

import base64
import json
import struct
import sys
from pathlib import Path

import pytest

# Make the onrecord package importable no matter where pytest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import WebSocketDisconnect  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from onrecord.chroma_store import FakeVectorStore  # noqa: E402
from onrecord.config import Settings  # noqa: E402
from onrecord.db import Store  # noqa: E402
from onrecord.embeddings import HashingTextEmbedder  # noqa: E402
from onrecord.face import FakeFaceEmbedder  # noqa: E402
from onrecord.ingest import create_app  # noqa: E402
from onrecord.stt import FakeTranscriber  # noqa: E402
from onrecord.voice import FakeVoiceEmbedder  # noqa: E402

SAMPLE_RATE = 16000
# 1 second of PCM16 silence: 16000 samples, 2 bytes each.
ONE_SECOND_PCM = b"\x00\x00" * SAMPLE_RATE
PHOTO_BYTES = b"\xff\xd8\xff" + b"fake-photo-bytes-for-enrollment"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path):
    """A TestClient backed by a fresh app, store, and vector store."""
    settings = Settings(data_dir=tmp_path, chroma_dir=tmp_path / "chroma")
    store = Store(settings)
    app = create_app(
        settings=settings,
        store=store,
        vectors=FakeVectorStore(),
        transcriber=FakeTranscriber(),
        face_embedder=FakeFaceEmbedder(),
        voice_embedder=FakeVoiceEmbedder(),
        text_embedder=HashingTextEmbedder(),
    )
    return TestClient(app, raise_server_exceptions=False)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _create_session(client: TestClient, title: str = "test session") -> str:
    resp = client.post("/sessions", json={"title": title})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    session_id = body.get("id") or body.get("session_id")
    assert session_id, body
    return str(session_id)


def _assert_no_raw_text(payload) -> None:
    """Recursively assert that no dict key named raw_text appears."""
    if isinstance(payload, dict):
        assert "raw_text" not in payload, payload
        for value in payload.values():
            _assert_no_raw_text(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_raw_text(item)
    # Belt and braces: the serialized form must not carry the key either.
    assert '"raw_text"' not in json.dumps(payload, default=str)


def _enroll_payload(name: str, photo: bytes = PHOTO_BYTES, voice: bytes = ONE_SECOND_PCM) -> dict:
    return {
        "name": name,
        "photo_b64": _b64(photo),
        "voice_b64": _b64(voice),
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# Session lifecycle over REST
# ---------------------------------------------------------------------------


def test_session_arm_start_then_repeat_start_conflicts(client):
    session_id = _create_session(client)

    resp = client.post(f"/sessions/{session_id}/arm")
    assert resp.status_code == 200, resp.text

    resp = client.post(f"/sessions/{session_id}/start")
    assert resp.status_code == 200, resp.text

    # Starting again is an out-of-order transition from RECORDING.
    resp = client.post(f"/sessions/{session_id}/start")
    assert resp.status_code == 409, resp.text

    # Arming again is also out of order once recording.
    resp = client.post(f"/sessions/{session_id}/arm")
    assert resp.status_code == 409, resp.text


def test_full_rest_flow_end_and_listing(client):
    session_id = _create_session(client, title="rest flow")

    assert client.post(f"/sessions/{session_id}/arm").status_code == 200
    assert client.post(f"/sessions/{session_id}/start").status_code == 200

    resp = client.post(f"/sessions/{session_id}/end")
    assert resp.status_code == 200, resp.text
    _assert_no_raw_text(resp.json())

    resp = client.get("/api/sessions")
    assert resp.status_code == 200, resp.text
    sessions = resp.json()
    assert isinstance(sessions, list)
    ids = [str(s.get("id") or s.get("session_id")) for s in sessions]
    assert session_id in ids

    resp = client.get(f"/api/sessions/{session_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "segments" in body
    assert isinstance(body["segments"], list)
    _assert_no_raw_text(body)


def test_get_unknown_session_is_404(client):
    resp = client.get("/api/sessions/no-such-session-id")
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------


def test_enroll_success_returns_person_name(client):
    resp = client.post("/api/enroll", json=_enroll_payload("Alice Example"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The person may be flat or nested; either way the name must surface.
    assert "Alice Example" in json.dumps(body)
    _assert_no_raw_text(body)


def test_enroll_same_bytes_second_person_allowed(client):
    first = client.post("/api/enroll", json=_enroll_payload("Alice Example"))
    assert first.status_code == 200, first.text

    # Identical photo and voice bytes for a different name: a separate
    # person, not a conflict.
    second = client.post("/api/enroll", json=_enroll_payload("Bob Example"))
    assert second.status_code == 200, second.text
    assert "Bob Example" in json.dumps(second.json())

    first_id = first.json().get("id")
    second_id = second.json().get("id")
    if first_id and second_id:
        assert first_id != second_id


def test_enroll_bad_base64_is_422(client):
    payload = _enroll_payload("Bad Base64")
    # A single data character is invalid base64 (bad length and padding),
    # so decoding raises instead of silently producing bytes.
    payload["photo_b64"] = "a"
    resp = client.post("/api/enroll", json=payload)
    assert resp.status_code == 422, resp.text


def test_enroll_empty_photo_is_422(client):
    payload = _enroll_payload("Empty Photo")
    payload["photo_b64"] = ""
    resp = client.post("/api/enroll", json=payload)
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Search on a fresh app
# ---------------------------------------------------------------------------


def test_search_on_fresh_app_returns_empty_with_reason(client):
    resp = client.post("/api/search", json={"query": "what was discussed"})
    assert resp.status_code == 200, resp.text
    assert resp.status_code != 500
    body = resp.json()
    assert body.get("status") == "EMPTY", body
    assert body.get("reason"), body
    if "results" in body:
        assert isinstance(body["results"], list)
    _assert_no_raw_text(body)


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------


def test_correct_unknown_segment_is_404(client):
    resp = client.post(
        "/api/segments/no-such-segment-id/correct",
        json={"person_id": "no-such-person-id"},
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# WebSocket ingest
# ---------------------------------------------------------------------------


def test_ws_unknown_session_is_rejected(client):
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ingest/audio?session_id=no-such-session"):
            pass
    assert excinfo.value.code == 4404


def test_ws_audio_flow_produces_segment_and_ends_session(client):
    session_id = _create_session(client, title="ws flow")

    ws_segments: list = []
    saw_session_ended = False

    with client.websocket_connect(f"/ingest/audio?session_id={session_id}") as ws:
        ws.send_json({"type": "arm"})
        ws.send_json({"type": "start"})

        # One binary frame: 16 byte header (seg_idx, frame_seq, ts)
        # followed by 1 second of PCM16 zeros.
        header = struct.pack("<IIQ", 0, 0, 0)
        assert len(header) == 16
        ws.send_bytes(header + ONE_SECOND_PCM)

        ws.send_json({"type": "end"})

        for _ in range(10):
            try:
                message = ws.receive_json()
            except WebSocketDisconnect:
                break
            _assert_no_raw_text(message)
            if message.get("type") == "segments":
                segments = message.get("segments") or []
                assert isinstance(segments, list)
                ws_segments.extend(segments)
            if message.get("type") == "session_ended":
                saw_session_ended = True
                break

    assert saw_session_ended

    resp = client.get(f"/api/sessions/{session_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_no_raw_text(body)
    rest_segments = body.get("segments") or []

    # At least one segment must be visible, via the WS segments message
    # or via the REST session view after the session ended.
    assert ws_segments or rest_segments, body
