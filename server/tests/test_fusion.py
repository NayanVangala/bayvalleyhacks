"""Tests for enrollment and identity fusion (Revision 2 contract).

Contract: face and voice must agree, else Unknown. When both a face
embedding and a voice embedding are provided, a name is returned only
if the top face hit and the top voice hit agree on the same person and
both scores clear their fusion thresholds. Disagreement, a missing hit,
or a low score means Unknown, never a guess.

Fakes only: FakeFaceEmbedder, FakeVoiceEmbedder, and FakeVectorStore.
The relational Store runs on a tmp dir built from Settings directly,
so no test touches the real data directory.
"""

from __future__ import annotations

import pytest

from onrecord.chroma_store import FakeVectorStore
from onrecord.config import Settings
from onrecord.db import Store
from onrecord.face import FakeFaceEmbedder
from onrecord.identity import EnrollmentService, FusionMatcher
from onrecord.interfaces import (
    ATTR_FACE_ONLY,
    ATTR_FUSION,
    ATTR_UNKNOWN,
    ATTR_VOICE_ONLY,
    COLLECTION_FACE,
    COLLECTION_VOICE,
)
from onrecord.voice import FakeVoiceEmbedder

# Distinct media per person. Fake embedders hash the raw bytes, so the
# exact same bytes give cosine 1.0 and different bytes give a near
# orthogonal vector. Voice pcm must be at least 0.3s at 16000 Hz mono
# 16-bit, which is 9600 bytes; every pcm below is 16000 bytes (0.5s).
PHOTO_A = b"fake-photo-alice"
PHOTO_B = b"fake-photo-bob"
PHOTO_UNKNOWN = b"fake-photo-never-enrolled"

PCM_A = b"\x10\x20" * 8000
PCM_B = b"\x30\x40" * 8000
PCM_UNKNOWN = b"\x50\x60" * 8000
PCM_TOO_SHORT = b"\x10\x20" * 100


@pytest.fixture()
def env(tmp_path):
    settings = Settings(data_dir=tmp_path, chroma_dir=tmp_path / "chroma")
    store = Store(settings)
    vectors = FakeVectorStore()
    face_embedder = FakeFaceEmbedder()
    voice_embedder = FakeVoiceEmbedder()
    enrollment = EnrollmentService(store, vectors, face_embedder, voice_embedder)
    matcher = FusionMatcher(vectors, settings)
    yield {
        "settings": settings,
        "store": store,
        "vectors": vectors,
        "face": face_embedder,
        "voice": voice_embedder,
        "enrollment": enrollment,
        "matcher": matcher,
    }
    store.close()


def _person_id(person):
    if isinstance(person, dict):
        return str(person["id"])
    return str(person.id)


# Enrollment


def test_enroll_person_creates_person_and_stores_prints(env):
    person = env["enrollment"].enroll_person("Alice", PHOTO_A, PCM_A)
    person_id = _person_id(person)

    assert env["store"].people_count() == 1
    stored = env["store"].get_person(person_id)
    assert stored is not None
    assert stored["name"] == "Alice"

    assert env["vectors"].count("face_prints") == 1
    assert env["vectors"].count("voice_prints") == 1
    assert env["vectors"].count(COLLECTION_FACE) == 1
    assert env["vectors"].count(COLLECTION_VOICE) == 1

    face_hit = env["vectors"].get(COLLECTION_FACE, "face:" + person_id)
    assert face_hit is not None
    assert face_hit.metadata.get("person_id") == person_id

    voice_hit = env["vectors"].get(COLLECTION_VOICE, "voice:" + person_id)
    assert voice_hit is not None
    assert voice_hit.metadata.get("person_id") == person_id


def test_enroll_empty_photo_raises_and_creates_no_person(env):
    with pytest.raises(ValueError):
        env["enrollment"].enroll_person("Alice", b"", PCM_A)

    assert env["store"].people_count() == 0
    assert env["vectors"].count(COLLECTION_FACE) == 0
    assert env["vectors"].count(COLLECTION_VOICE) == 0


def test_enroll_too_short_voice_raises_and_creates_no_person(env):
    assert len(PCM_TOO_SHORT) < 16000 * 2 * 0.3
    with pytest.raises(ValueError):
        env["enrollment"].enroll_person("Alice", PHOTO_A, PCM_TOO_SHORT)

    assert env["store"].people_count() == 0
    assert env["vectors"].count(COLLECTION_FACE) == 0
    assert env["vectors"].count(COLLECTION_VOICE) == 0


# Fusion: agreement


def test_identify_same_media_returns_fusion_with_scores_near_one(env):
    person = env["enrollment"].enroll_person("Alice", PHOTO_A, PCM_A)
    person_id = _person_id(person)

    result = env["matcher"].identify(
        face_embedding=env["face"].embed_image(PHOTO_A),
        voice_embedding=env["voice"].embed_pcm(PCM_A),
    )

    assert result.person_id == person_id
    assert result.source == ATTR_FUSION
    assert env["store"].get_person(result.person_id)["name"] == "Alice"
    assert result.face_person_id == person_id
    assert result.voice_person_id == person_id
    assert result.face_score == pytest.approx(1.0, abs=1e-6)
    assert result.voice_score == pytest.approx(1.0, abs=1e-6)


def test_identify_from_media_names_the_person(env):
    person = env["enrollment"].enroll_person("Alice", PHOTO_A, PCM_A)
    person_id = _person_id(person)

    result = env["matcher"].identify_from_media(
        PHOTO_A, PCM_A, env["face"], env["voice"]
    )

    assert result.person_id == person_id
    assert result.source == ATTR_FUSION
    assert env["store"].get_person(result.person_id)["name"] == "Alice"
    assert result.face_score == pytest.approx(1.0, abs=1e-6)
    assert result.voice_score == pytest.approx(1.0, abs=1e-6)


# Fusion: disagreement and unknown media


def test_identify_disagreement_returns_unknown_with_both_ids(env):
    person_a = env["enrollment"].enroll_person("Alice", PHOTO_A, PCM_A)
    person_b = env["enrollment"].enroll_person("Bob", PHOTO_B, PCM_B)
    a_id = _person_id(person_a)
    b_id = _person_id(person_b)
    assert a_id != b_id

    result = env["matcher"].identify(
        face_embedding=env["face"].embed_image(PHOTO_A),
        voice_embedding=env["voice"].embed_pcm(PCM_B),
    )

    assert result.person_id is None
    assert result.source == ATTR_UNKNOWN
    assert result.face_person_id == a_id
    assert result.voice_person_id == b_id


def test_identify_unknown_person_media_returns_unknown(env):
    env["enrollment"].enroll_person("Alice", PHOTO_A, PCM_A)

    result = env["matcher"].identify(
        face_embedding=env["face"].embed_image(PHOTO_UNKNOWN),
        voice_embedding=env["voice"].embed_pcm(PCM_UNKNOWN),
    )

    assert result.person_id is None
    assert result.source == ATTR_UNKNOWN


def test_identify_from_media_unknown_person_returns_unknown(env):
    env["enrollment"].enroll_person("Alice", PHOTO_A, PCM_A)

    result = env["matcher"].identify_from_media(
        PHOTO_UNKNOWN, PCM_UNKNOWN, env["face"], env["voice"]
    )

    assert result.person_id is None
    assert result.source == ATTR_UNKNOWN


# Single modality and neither


def test_identify_face_only(env):
    person = env["enrollment"].enroll_person("Alice", PHOTO_A, PCM_A)
    person_id = _person_id(person)

    result = env["matcher"].identify(
        face_embedding=env["face"].embed_image(PHOTO_A),
    )

    assert result.person_id == person_id
    assert result.source == ATTR_FACE_ONLY
    assert result.face_score == pytest.approx(1.0, abs=1e-6)


def test_identify_voice_only(env):
    person = env["enrollment"].enroll_person("Alice", PHOTO_A, PCM_A)
    person_id = _person_id(person)

    result = env["matcher"].identify(
        voice_embedding=env["voice"].embed_pcm(PCM_A),
    )

    assert result.person_id == person_id
    assert result.source == ATTR_VOICE_ONLY
    assert result.voice_score == pytest.approx(1.0, abs=1e-6)


def test_identify_neither_returns_unknown(env):
    env["enrollment"].enroll_person("Alice", PHOTO_A, PCM_A)

    result = env["matcher"].identify()

    assert result.person_id is None
    assert result.source == ATTR_UNKNOWN

    result_explicit = env["matcher"].identify(
        face_embedding=None, voice_embedding=None
    )
    assert result_explicit.person_id is None
    assert result_explicit.source == ATTR_UNKNOWN
