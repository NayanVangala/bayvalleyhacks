"""Contract tests for POST /api/enroll and POST /api/people/enroll.

These pin the wire contract in /tmp/enroll-contract.md (the source of
truth the Swift bridge codes against): valid requests succeed and
queue face/voice prints, every malformed payload is a 422, oversized
payloads are 413.

The app is built with create_app and injected fakes: a real Store on a
temporary DATA_DIR, FakeVectorStore, FakeTranscriber, FakeFaceEmbedder,
FakeVoiceEmbedder, HashingTextEmbedder. No network, no ML models.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

# Make the onrecord package importable no matter where pytest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from onrecord import ingest as ingest_module  # noqa: E402
from onrecord.chroma_store import FakeVectorStore  # noqa: E402
from onrecord.config import Settings  # noqa: E402
from onrecord.db import Store  # noqa: E402
from onrecord.embeddings import HashingTextEmbedder  # noqa: E402
from onrecord.face import FakeFaceEmbedder  # noqa: E402
from onrecord.ingest import create_app  # noqa: E402
from onrecord.interfaces import COLLECTION_FACE, COLLECTION_VOICE  # noqa: E402
from onrecord.stt import FakeTranscriber  # noqa: E402
from onrecord.voice import FakeVoiceEmbedder  # noqa: E402

SAMPLE_RATE = 16000
# 1 second of raw PCM16 mono @16kHz: the minimum the server accepts.
ONE_SECOND_PCM = b"\x00\x00" * SAMPLE_RATE
TEN_SECONDS_PCM = ONE_SECOND_PCM * 10
# A photo the server accepts must start with the JPEG magic bytes.
JPEG_PHOTO = b"\xff\xd8\xff" + b"fake-enrollment-photo"

ENROLL_ENDPOINTS = ["/api/enroll", "/api/people/enroll"]


@pytest.fixture()
def app_and_vectors(tmp_path):
    """A (TestClient, FakeVectorStore) pair sharing one fresh app."""
    settings = Settings(data_dir=tmp_path, chroma_dir=tmp_path / "chroma")
    store = Store(settings)
    vectors = FakeVectorStore()
    app = create_app(
        settings=settings,
        store=store,
        vectors=vectors,
        transcriber=FakeTranscriber(),
        face_embedder=FakeFaceEmbedder(),
        voice_embedder=FakeVoiceEmbedder(),
        text_embedder=HashingTextEmbedder(),
    )
    return TestClient(app, raise_server_exceptions=False), vectors


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _payload(name="Ada Example", photo=JPEG_PHOTO, voice=TEN_SECONDS_PCM,
             notes=None, **overrides):
    body = {
        "name": name,
        "photo_b64": _b64(photo),
        "voice_b64": _b64(voice),
    }
    if notes is not None:
        body["notes"] = notes
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Success: person created, face + voice prints queued
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ENROLL_ENDPOINTS)
def test_enroll_success_creates_person_and_prints(app_and_vectors, endpoint):
    client, vectors = app_and_vectors
    resp = client.post(endpoint, json=_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("name") == "Ada Example", body
    person_id = body.get("id")
    assert person_id, body

    # Both biometric prints are queued for the new person.
    face_hit = vectors.get(COLLECTION_FACE, "face:" + str(person_id))
    voice_hit = vectors.get(COLLECTION_VOICE, "voice:" + str(person_id))
    assert face_hit is not None
    assert voice_hit is not None

    # The person is listed back by the people API.
    people = client.get("/api/people").json()
    ids = {str(p.get("id")) for p in people}
    assert str(person_id) in ids


@pytest.mark.parametrize("endpoint", ENROLL_ENDPOINTS)
def test_enroll_notes_optional(app_and_vectors, endpoint):
    client, _ = app_and_vectors
    resp = client.post(endpoint, json=_payload(notes="hello notes"))
    assert resp.status_code == 200, resp.text
    assert "Ada Example" in resp.text


def test_enroll_alias_keys_accepted(app_and_vectors):
    client, vectors = app_and_vectors
    resp = client.post(
        "/api/enroll",
        json={
            "name": "Alias User",
            "photo": _b64(JPEG_PHOTO),
            "voice_pcm_b64": _b64(TEN_SECONDS_PCM),
        },
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 422: missing / malformed fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ENROLL_ENDPOINTS)
@pytest.mark.parametrize("missing", ["name", "photo_b64", "voice_b64"])
def test_enroll_missing_field_is_422(app_and_vectors, endpoint, missing):
    client, _ = app_and_vectors
    payload = _payload()
    del payload[missing]
    resp = client.post(endpoint, json=payload)
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("endpoint", ENROLL_ENDPOINTS)
def test_enroll_blank_name_is_422(app_and_vectors, endpoint):
    client, _ = app_and_vectors
    resp = client.post(endpoint, json=_payload(name="   "))
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("endpoint", ENROLL_ENDPOINTS)
def test_enroll_invalid_base64_is_422(app_and_vectors, endpoint):
    client, _ = app_and_vectors
    resp = client.post(endpoint, json=_payload(photo_b64="a"))
    assert resp.status_code == 422, resp.text
    resp = client.post(endpoint, json=_payload(voice_b64="!!!not-base64!!!"))
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("endpoint", ENROLL_ENDPOINTS)
def test_enroll_non_string_payloads_are_422(app_and_vectors, endpoint):
    client, _ = app_and_vectors
    resp = client.post(endpoint, json=_payload(photo_b64=123))
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("endpoint", ENROLL_ENDPOINTS)
def test_enroll_empty_photo_is_422(app_and_vectors, endpoint):
    client, _ = app_and_vectors
    resp = client.post(endpoint, json=_payload(photo=b""))
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("endpoint", ENROLL_ENDPOINTS)
def test_enroll_non_jpeg_photo_is_422(app_and_vectors, endpoint):
    client, _ = app_and_vectors
    resp = client.post(endpoint, json=_payload(photo=b"\x89PNG" + b"x" * 100))
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("endpoint", ENROLL_ENDPOINTS)
def test_enroll_empty_voice_is_422(app_and_vectors, endpoint):
    client, _ = app_and_vectors
    resp = client.post(endpoint, json=_payload(voice=b""))
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("endpoint", ENROLL_ENDPOINTS)
def test_enroll_short_voice_is_422(app_and_vectors, endpoint):
    client, _ = app_and_vectors
    # Half a second of PCM: well under the 1 second minimum.
    resp = client.post(endpoint, json=_payload(voice=b"\x00" * 16000))
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("endpoint", ENROLL_ENDPOINTS)
def test_enroll_odd_length_voice_is_422(app_and_vectors, endpoint):
    client, _ = app_and_vectors
    # Over the minimum length but not a whole number of 16-bit samples.
    resp = client.post(
        endpoint, json=_payload(voice=b"\x00" * (ingest_module.MIN_ENROLL_VOICE_BYTES + 1))
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("endpoint", ENROLL_ENDPOINTS)
def test_enroll_wav_voice_is_422(app_and_vectors, endpoint):
    client, _ = app_and_vectors
    # A WAV file is not raw PCM: the RIFF header must be stripped first.
    wav = b"RIFF" + b"\x00" * 40 + b"WAVEfmt " + b"\x00" * (ingest_module.MIN_ENROLL_VOICE_BYTES)
    resp = client.post(endpoint, json=_payload(voice=wav))
    assert resp.status_code == 422, resp.text
    assert "WAV" in resp.json().get("detail", "")


def test_enroll_one_second_voice_is_the_minimum(app_and_vectors):
    client, _ = app_and_vectors
    resp = client.post("/api/enroll", json=_payload(voice=ONE_SECOND_PCM))
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 413: oversized payloads
# ---------------------------------------------------------------------------


def test_enroll_oversize_photo_is_413(app_and_vectors, monkeypatch):
    client, _ = app_and_vectors
    monkeypatch.setattr(ingest_module, "MAX_ENROLL_PHOTO_BYTES", 10)
    resp = client.post("/api/enroll", json=_payload(photo=b"\xff\xd8\xff" + b"x" * 100))
    assert resp.status_code == 413, resp.text


def test_enroll_oversize_voice_is_413(app_and_vectors, monkeypatch):
    client, _ = app_and_vectors
    monkeypatch.setattr(ingest_module, "MAX_ENROLL_VOICE_BYTES", 10)
    resp = client.post("/api/enroll", json=_payload())
    assert resp.status_code == 413, resp.text


# ---------------------------------------------------------------------------
# Failure leaves no half enrolled person
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ENROLL_ENDPOINTS)
def test_enroll_failure_creates_no_person(app_and_vectors, endpoint):
    client, _ = app_and_vectors
    resp = client.post(endpoint, json=_payload(photo=b"\x89PNG" + b"x" * 100))
    assert resp.status_code == 422, resp.text
    people = client.get("/api/people").json()
    assert people == [], people
