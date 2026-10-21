"""Handshake-correction tests for the OnRecord bridge wire protocol.

Proves the server accepts the CORRECTED iOS bridge behavior (the Swift
blockers from the glasses-fix review: session creation and the JSON
base64 enrollment shape at /api/enroll):

1. A WebSocket hello carrying a fresh phone-generated session UUID
   auto-registers the session and gets a welcome (no 4404).
2. A malformed hello (missing session_id) is still rejected.
3. POST /sessions with {"id": "<uuid>"} echoes that id back.
4. POST /api/enroll accepts JSON {name, photo_b64, voice_b64}; missing
   fields -> 422; garbage base64 -> 422.
5. A bridge audio frame with the PREROLL flag (0x01, PROTOCOL.md 5.3)
   is accepted and routed into the server-side pre-roll ring, never
   padded, never rejected for its flag.
6. Legacy protocol frames still work (no regression).

The app is built exactly like tests/test_ingest.py: create_app with a
real Store on a temporary DATA_DIR plus the build's fake vector store,
transcriber, face/voice embedders, and text embedder. No network, no
ML models.

Run from the server directory:

    .venv/bin/python -m pytest tests/test_bridge_handshake.py -q
"""

from __future__ import annotations

import base64
import math
import struct
import sys
import uuid
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
from onrecord.ingest import BRIDGE_HEADER, KIND_AUDIO, create_app  # noqa: E402
from onrecord.stt import FakeTranscriber  # noqa: E402
from onrecord.voice import FakeVoiceEmbedder  # noqa: E402

SAMPLE_RATE = 16000
FRAME_BYTES = 3200  # one 100 ms PCM16 mono frame at 16 kHz
FLAG_PREROLL = 0x01  # PROTOCOL.md section 5.3, bit 0
FLAG_FINAL = 0x02  # PROTOCOL.md section 5.3, bit 1


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


def _fresh_uuid() -> str:
    return str(uuid.uuid4())


def _create_session(client: TestClient, title: str = "handshake test") -> str:
    resp = client.post("/sessions", json={"title": title})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    session_id = body.get("id") or body.get("session_id")
    assert session_id, body
    return str(session_id)


def _hello(session_id: str | None, **extra) -> dict:
    """A PROTOCOL.md section 3.1 hello frame; session_id=None omits it."""
    msg = {
        "type": "hello",
        "protocol": 1,
        "app": "insight-bridge",
        "app_version": "1.0",
        "source": "phone",
        "audio_format": {"codec": "pcm16", "sample_rate": SAMPLE_RATE, "channels": 1},
    }
    if session_id is not None:
        msg["session_id"] = session_id
    msg.update(extra)
    return msg


def _bridge_audio_frame(
    flags: int,
    seg_idx: int = 0,
    frame_seq: int = 0,
    capture_ts_ms: int = 0,
    payload: bytes = b"",
) -> bytes:
    header = BRIDGE_HEADER.pack(b"OR", KIND_AUDIO, flags, seg_idx, frame_seq, capture_ts_ms)
    assert len(header) == 16
    return header + payload


# Distinctive non-zero PCM patterns, so the test can tell real frame
# bytes apart from zero padding.
PATTERN_FULL = bytes([0x1A, 0x2B]) * (FRAME_BYTES // 2)
PATTERN_SHORT = bytes([0x3C, 0x4D]) * 800  # 1600 bytes: a short final frame


def _sine_pcm(seconds: float = 2.0, freq: float = 440.0) -> bytes:
    """A few seconds of synthetic raw PCM16 mono, 16 kHz, for enrollment."""
    n = int(seconds * SAMPLE_RATE)
    return struct.pack(
        "<%dh" % n,
        *(int(12000 * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)) for i in range(n)),
    )


# A tiny but structurally real JPEG: SOI, JFIF APP0, EOI. The fake face
# embedder only needs non-empty bytes, but the corrected client sends a
# real JPEG and the test pins that shape.
TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" b"\xff\xd9"
)


def _enroll_payload(name="Handshake Person", photo=TINY_JPEG, voice=None) -> dict:
    return {
        "name": name,
        "photo_b64": _b64(photo),
        "voice_b64": _b64(voice if voice is not None else _sine_pcm()),
    }


def _runtime_entry(client: TestClient, session_id: str) -> dict:
    return client.app.state.runtime[session_id]


# ---------------------------------------------------------------------------
# 1. Hello with a fresh phone-generated UUID auto-registers the session
# ---------------------------------------------------------------------------


def test_ws_hello_with_fresh_uuid_auto_registers_and_welcomes(client):
    fresh = _fresh_uuid()
    connect = client.websocket_connect(f"/ingest/audio?session_id={fresh}")
    try:
        ws = connect.__enter__()
    except WebSocketDisconnect as exc:
        pytest.fail(
            "server closed the socket with code %s for a fresh phone-generated "
            "session id instead of auto-registering it and replying welcome"
            % (exc.code,)
        )
    try:
        ws.send_json(_hello(fresh))
        reply = ws.receive_json()
    finally:
        connect.__exit__(None, None, None)

    assert reply.get("type") == "welcome", reply
    assert reply.get("session_id") == fresh, reply
    assert reply.get("ok") is True, reply
    assert reply.get("server_time"), reply

    # Auto-registration means the session exists server side afterwards,
    # not just that this socket was welcomed.
    resp = client.get(f"/api/sessions/{fresh}")
    assert resp.status_code == 200, resp.text
    assert str(resp.json().get("id")) == fresh


def test_ws_hello_reconnect_same_uuid_resumes(client):
    fresh = _fresh_uuid()
    with client.websocket_connect(f"/ingest/audio?session_id={fresh}") as ws:
        ws.send_json(_hello(fresh))
        first = ws.receive_json()
    assert first.get("type") == "welcome", first

    # Same UUID on a new socket is a resume, not a new session.
    with client.websocket_connect(f"/ingest/audio?session_id={fresh}") as ws:
        ws.send_json(_hello(fresh))
        second = ws.receive_json()
    assert second.get("type") == "welcome", second
    assert second.get("session_id") == fresh, second


# ---------------------------------------------------------------------------
# 2. Malformed hello (missing session_id) is still rejected
# ---------------------------------------------------------------------------


def test_ws_hello_missing_session_id_is_rejected(client):
    session_id = _create_session(client)
    with client.websocket_connect(f"/ingest/audio?session_id={session_id}") as ws:
        # Deliberately no session_id field at all.
        ws.send_json(_hello(None))
        try:
            reply = ws.receive_json()
        except WebSocketDisconnect:
            return  # Closed without a reply: rejected.
        assert reply.get("type") == "error", (
            "malformed hello (missing session_id) was not rejected, "
            "server answered: %r" % (reply,)
        )
        assert reply.get("fatal") is True, reply


def test_ws_hello_session_id_mismatch_is_rejected(client):
    session_id = _create_session(client)
    other = _fresh_uuid()
    with client.websocket_connect(f"/ingest/audio?session_id={session_id}") as ws:
        # Hello names a different session than the query string.
        ws.send_json(_hello(other))
        try:
            reply = ws.receive_json()
        except WebSocketDisconnect:
            return  # Closed without a reply: rejected.
        assert reply.get("type") == "error", (
            "hello with a mismatched session_id was not rejected, "
            "server answered: %r" % (reply,)
        )


# ---------------------------------------------------------------------------
# 3. POST /sessions with an explicit id echoes that id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["/sessions", "/api/sessions"])
def test_post_sessions_with_explicit_id_returns_that_id(client, route):
    wanted = _fresh_uuid()
    resp = client.post(route, json={"id": wanted, "title": "phone-created session"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("id") == wanted, body

    # The echoed id is immediately usable: status works and a hello for
    # it is welcomed (the corrected phone flow: POST then connect).
    status = client.get(f"/sessions/{wanted}/status")
    assert status.status_code == 200, status.text
    with client.websocket_connect(f"/ingest/audio?session_id={wanted}") as ws:
        ws.send_json(_hello(wanted))
        reply = ws.receive_json()
    assert reply.get("type") == "welcome", reply
    assert reply.get("session_id") == wanted, reply


# ---------------------------------------------------------------------------
# 4. JSON enrollment at /api/enroll
# ---------------------------------------------------------------------------


def test_enroll_json_shape_succeeds(client):
    resp = client.post("/api/enroll", json=_enroll_payload("Handshake Person"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "Handshake Person" in str(body), body
    assert body.get("id") or body.get("person_id"), body


@pytest.mark.parametrize(
    "drop",
    ["name", "photo_b64", "voice_b64"],
)
def test_enroll_missing_field_is_422(client, drop):
    payload = _enroll_payload()
    del payload[drop]
    resp = client.post("/api/enroll", json=payload)
    assert resp.status_code == 422, resp.text


def test_enroll_empty_body_is_422(client):
    resp = client.post("/api/enroll", json={})
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("field", ["photo_b64", "voice_b64"])
@pytest.mark.parametrize("bad", ["!!!not-base64!!!", "a"])
def test_enroll_garbage_base64_is_422(client, field, bad):
    payload = _enroll_payload()
    payload[field] = bad
    resp = client.post("/api/enroll", json=payload)
    assert resp.status_code == 422, resp.text


def test_enroll_non_string_payload_is_422(client):
    payload = _enroll_payload()
    payload["photo_b64"] = 12345
    resp = client.post("/api/enroll", json=payload)
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# 5. Bridge audio frame with PREROLL flag 0x01
# ---------------------------------------------------------------------------


def test_bridge_audio_frame_with_preroll_flag_is_accepted_not_padded(client):
    session_id = _create_session(client)
    with client.websocket_connect(f"/ingest/audio?session_id={session_id}") as ws:
        ws.send_json(_hello(session_id))
        welcome = ws.receive_json()
        assert welcome.get("type") == "welcome", welcome

        # Full 100 ms frame with the PREROLL bit set (PROTOCOL.md 5.3).
        # The session is IDLE, so this is pre-roll audio for segment 0.
        ws.send_bytes(_bridge_audio_frame(FLAG_PREROLL, payload=PATTERN_FULL))

        # A short final frame carrying PREROLL|FINAL: the last audio of
        # a segment may be shorter than 3200 bytes and must be kept
        # as-is, never padded.
        ws.send_bytes(
            _bridge_audio_frame(
                FLAG_PREROLL | FLAG_FINAL,
                frame_seq=1,
                capture_ts_ms=100,
                payload=PATTERN_SHORT,
            )
        )

        # Resend the first frame verbatim: a reconnect replay must
        # dedupe, proving the first send was accepted and recorded.
        ws.send_bytes(_bridge_audio_frame(FLAG_PREROLL, payload=PATTERN_FULL))

        # Replies are FIFO: a BAD_PROTOCOL rejection of any frame above
        # would arrive before this pong.
        ws.send_json({"type": "ping", "t0_mono_ms": 100})
        reply = ws.receive_json()
        assert reply.get("type") == "pong", (
            "expected pong after pre-roll frames, server answered: %r" % (reply,)
        )

    entry = _runtime_entry(client, session_id)
    assert entry.get("malformed_frames", 0) == 0, entry
    assert entry.get("duplicate_frames", 0) == 1, entry

    # Marked as pre-roll: both frames landed byte-exact in the
    # server-side pre-roll ring, oldest first, with no padding added.
    ring = entry["controller"].preroll.snapshot()
    assert ring == PATTERN_FULL + PATTERN_SHORT, (
        "pre-roll ring holds %d bytes, expected %d of exact frame bytes"
        % (len(ring), len(PATTERN_FULL) + len(PATTERN_SHORT))
    )


def test_bridge_audio_frame_preroll_bit_alone_never_rejects(client):
    """Flags are informational (PROTOCOL.md 5.3): 0x01 must not error."""
    session_id = _create_session(client)
    with client.websocket_connect(f"/ingest/audio?session_id={session_id}") as ws:
        ws.send_json(_hello(session_id))
        assert ws.receive_json().get("type") == "welcome"

        for flags in (FLAG_PREROLL, FLAG_PREROLL | FLAG_FINAL, 0x00):
            payload = PATTERN_FULL if flags != (FLAG_PREROLL | FLAG_FINAL) else PATTERN_SHORT
            ws.send_bytes(
                _bridge_audio_frame(flags, frame_seq=flags, payload=payload)
            )
        ws.send_json({"type": "ping", "t0_mono_ms": 7})
        reply = ws.receive_json()
        assert reply.get("type") == "pong", reply

    entry = _runtime_entry(client, session_id)
    assert entry.get("malformed_frames", 0) == 0, entry


# ---------------------------------------------------------------------------
# 6. Legacy protocol frames still work (no regression)
# ---------------------------------------------------------------------------


def test_legacy_binary_and_control_flow_still_works(client):
    session_id = _create_session(client, title="legacy regression")

    saw_segments = False
    saw_session_ended = False

    with client.websocket_connect(f"/ingest/audio?session_id={session_id}") as ws:
        ws.send_json({"type": "arm"})
        ws.send_json({"type": "start"})

        # Legacy frame: 16 byte "<IIQ" header (seg_idx, frame_seq,
        # capture_ts_ms, little endian) plus raw PCM16 audio.
        header = struct.pack("<IIQ", 0, 0, 0)
        assert len(header) == 16
        ws.send_bytes(header + b"\x00\x00" * SAMPLE_RATE)

        ws.send_json({"type": "end"})

        for _ in range(10):
            try:
                message = ws.receive_json()
            except WebSocketDisconnect:
                break
            if message.get("type") == "segments":
                assert isinstance(message.get("segments"), list)
                saw_segments = True
            if message.get("type") == "session_ended":
                saw_session_ended = True
                break

    assert saw_session_ended, "legacy end did not produce session_ended"

    resp = client.get(f"/api/sessions/{session_id}")
    assert resp.status_code == 200, resp.text
    rest_segments = resp.json().get("segments") or []
    assert saw_segments or rest_segments, "legacy audio produced no segment"
