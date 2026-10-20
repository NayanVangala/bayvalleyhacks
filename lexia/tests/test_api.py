"""Contract tests for the lexia.api FastAPI app.

The suite runs with LEXIA_OCR_ENGINE=fake and LEXIA_TTS_ENGINE=fake
(see conftest.py), so every request below exercises the fake engines
and needs no tesseract binary and no espeak.

Contracts covered:
* GET /health returns status ok plus the active engine names.
* POST /read with a base64 1x1 PNG returns 200 with a session_id,
  non-empty chunks, and engine "fake".
* GET /read/{id} returns the same session that POST /read created.
* GET /read/doesnotexist returns 404.
* POST /read/{id}/speak returns an audio_url starting with /audio/
  and a duration_seconds greater than zero.
"""

import pytest

pytest.importorskip("fastapi")

try:
    from fastapi.testclient import TestClient  # noqa: E402
except (ImportError, RuntimeError) as exc:
    pytest.skip(
        "fastapi TestClient unavailable in this venv (%s)" % exc,
        allow_module_level=True,
    )

PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8Xw8AAwMCAO+ip1sAAAAASUVORK5CYII="
)


def load_app():
    import types

    try:
        from lexia.api.app import app as fastapi_app

        return fastapi_app
    except ImportError:
        pass
    from lexia import api

    candidate = getattr(api, "app", None)
    if candidate is not None and not isinstance(candidate, types.ModuleType):
        return candidate
    create_app = getattr(api, "create_app", None)
    assert create_app is not None, "lexia.api exposes neither app nor create_app"
    return create_app()


@pytest.fixture(scope="module")
def client():
    return TestClient(load_app())


def chunk_texts(chunks):
    texts = []
    for chunk in chunks:
        if isinstance(chunk, str):
            texts.append(chunk)
        elif isinstance(chunk, dict):
            texts.append(chunk.get("text", ""))
        else:
            texts.append(getattr(chunk, "text", str(chunk)))
    return texts


def body_engine(body):
    for key in ("engine", "ocr_engine", "ocrEngine"):
        if key in body:
            return body[key]
    return None


@pytest.fixture(scope="module")
def created_session(client):
    response = client.post("/read", json={"image_base64": PNG_1X1_BASE64})
    assert response.status_code == 200, response.text
    return response.json()


def test_health_ok_and_engine_names(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body.get("status") == "ok"
    assert "fake" in str(body).lower(), "health should name the fake engines: %r" % body


def test_post_read_returns_session_chunks_and_engine(created_session):
    body = created_session
    session_id = body.get("session_id") or body.get("id")
    assert session_id, "POST /read should return a session_id: %r" % body
    chunks = body.get("chunks")
    assert chunks, "POST /read should return non-empty chunks"
    assert any(text.strip() for text in chunk_texts(chunks))
    engine = body_engine(body)
    if engine is not None:
        assert engine == "fake"
    else:
        assert "fake" in str(body).lower()


def test_get_read_returns_same_session(client, created_session):
    session_id = created_session.get("session_id") or created_session.get("id")
    response = client.get("/read/%s" % session_id)
    assert response.status_code == 200
    body = response.json()
    assert (body.get("session_id") or body.get("id")) == session_id
    assert chunk_texts(body.get("chunks") or []) == chunk_texts(
        created_session.get("chunks") or []
    )


def test_get_read_unknown_session_is_404(client):
    response = client.get("/read/doesnotexist")
    assert response.status_code == 404


def test_post_speak_returns_audio_url_and_duration(client, created_session):
    session_id = created_session.get("session_id") or created_session.get("id")
    url = "/read/%s/speak" % session_id

    response = client.post(url)
    if response.status_code == 422:
        response = client.post(url, json={"chunk_index": 0})
    if response.status_code == 422:
        response = client.post(url, json={"index": 0})
    if response.status_code == 422:
        response = client.post(url, json={})

    assert response.status_code == 200, response.text
    body = response.json()
    audio_url = body.get("audio_url") or body.get("audioUrl") or body.get("url")
    assert audio_url is not None, "speak should return an audio_url: %r" % body
    assert audio_url.startswith("/audio/")
    assert body.get("duration_seconds") is not None
    assert body["duration_seconds"] > 0
