"""Tests for onrecord.pipeline.Pipeline.

Fixtures are built locally in this file: a real Store on a tmp
DATA_DIR, a FakeVectorStore, a scripted FakeTranscriber,
FakeFaceEmbedder, FakeVoiceEmbedder, FusionMatcher, and
HashingTextEmbedder. No conftest.py is assumed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onrecord.chroma_store import FakeVectorStore
from onrecord.config import Settings
from onrecord.db import Store
from onrecord.embeddings import HashingTextEmbedder
from onrecord.face import FakeFaceEmbedder
from onrecord.identity import EnrollmentService, FusionMatcher
from onrecord.interfaces import (
    COLLECTION_CHUNKS,
    GATE_REVIEW,
    GATE_SILENCE,
    GATE_VERIFIED,
    RawSegment,
    SealedSegment,
)
from onrecord.pipeline import Pipeline
from onrecord.stt import FakeTranscriber
from onrecord.voice import FakeVoiceEmbedder

SAMPLE_RATE = 16000


def _make_pcm(seconds=1.0, pattern=b"\x10\x20"):
    """PCM16 mono bytes of the given duration, deterministic content."""
    samples = int(seconds * SAMPLE_RATE)
    return pattern * samples


def _make_settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        database_url=None,
        chroma_dir=tmp_path / "chroma",
    )


def _make_pipeline(store, vectors, settings, transcriber):
    text_embedder = HashingTextEmbedder()
    pipeline = Pipeline(
        store=store,
        vectors=vectors,
        transcriber=transcriber,
        face_embedder=FakeFaceEmbedder(),
        voice_embedder=FakeVoiceEmbedder(),
        matcher=FusionMatcher(vectors, settings),
        text_embedder=text_embedder,
        sample_rate=SAMPLE_RATE,
    )
    return pipeline, text_embedder


@pytest.fixture()
def world(tmp_path):
    settings = _make_settings(tmp_path)
    store = Store(settings)
    vectors = FakeVectorStore()
    session = store.create_session(title="pipeline session")
    try:
        yield {
            "settings": settings,
            "store": store,
            "vectors": vectors,
            "session": session,
        }
    finally:
        store.close()


def test_verified_segment_is_stored_and_embedded_into_chunks(world):
    store = world["store"]
    vectors = world["vectors"]
    session = world["session"]

    text = "the budget review starts at noon tomorrow"
    raw = RawSegment(
        text=text,
        avg_logprob=-0.2,
        no_speech_prob=0.05,
        start=0.5,
        end=2.0,
    )
    transcriber = FakeTranscriber(script=[[raw]])
    pipeline, text_embedder = _make_pipeline(
        store, vectors, world["settings"], transcriber
    )

    assert vectors.count(COLLECTION_CHUNKS) == 0

    pcm = _make_pcm(seconds=2.0)
    sealed = SealedSegment(seq=1, pcm=pcm, duration_s=2.0)
    rows = pipeline.process_segment(session["id"], sealed, base_offset_s=10.0)

    assert len(rows) == 1
    row = rows[0]
    assert row["gate_status"] == GATE_VERIFIED
    assert row["text"] == text
    assert row["raw_text"] == text
    assert row["session_id"] == session["id"]

    # The stored row in the database matches the returned row.
    stored = store.get_segment(row["id"])
    assert stored is not None
    assert stored["gate_status"] == GATE_VERIFIED
    assert stored["text"] == text

    # VERIFIED text IS embedded into the chunk collection.
    assert vectors.count(COLLECTION_CHUNKS) == 1
    hits = vectors.query(
        COLLECTION_CHUNKS, text_embedder.embed(text), n=5
    )
    assert hits
    assert hits[0].id == row["id"]
    assert hits[0].document == text
    assert hits[0].score == pytest.approx(1.0)


def test_review_segment_stores_uncertain_display_and_is_not_embedded(world):
    store = world["store"]
    vectors = world["vectors"]
    session = world["session"]

    raw_text = "mumbled words that are hard to make out"
    raw = RawSegment(
        text=raw_text,
        avg_logprob=-0.8,
        no_speech_prob=0.05,
        start=0.0,
        end=1.0,
    )
    transcriber = FakeTranscriber(script=[[raw]])
    pipeline, _text_embedder = _make_pipeline(
        store, vectors, world["settings"], transcriber
    )

    pcm = _make_pcm(seconds=1.0)
    sealed = SealedSegment(seq=2, pcm=pcm, duration_s=1.0)
    rows = pipeline.process_segment(session["id"], sealed)

    assert len(rows) == 1
    row = rows[0]
    assert row["gate_status"] == GATE_REVIEW
    assert row["text"] == "[uncertain]"
    assert row["raw_text"] == raw_text

    stored = store.get_segment(row["id"])
    assert stored is not None
    assert stored["gate_status"] == GATE_REVIEW
    assert stored["text"] == "[uncertain]"
    assert stored["raw_text"] == raw_text

    # REVIEW text is NOT embedded into chunks.
    assert vectors.count(COLLECTION_CHUNKS) == 0


def test_empty_transcription_yields_exactly_one_silence_row(world):
    store = world["store"]
    vectors = world["vectors"]
    session = world["session"]

    transcriber = FakeTranscriber(script=[[]])
    pipeline, _text_embedder = _make_pipeline(
        store, vectors, world["settings"], transcriber
    )

    pcm = _make_pcm(seconds=1.5)
    sealed = SealedSegment(seq=3, pcm=pcm, duration_s=1.5)
    rows = pipeline.process_segment(session["id"], sealed, base_offset_s=3.0)

    assert len(rows) == 1
    row = rows[0]
    assert row["gate_status"] == GATE_SILENCE
    assert row["text"] == ""

    stored_segments = store.list_segments(session["id"])
    assert len(stored_segments) == 1
    assert stored_segments[0]["id"] == row["id"]
    assert stored_segments[0]["gate_status"] == GATE_SILENCE

    assert vectors.count(COLLECTION_CHUNKS) == 0


def test_enrolled_person_is_attributed_when_face_and_voice_agree(world):
    store = world["store"]
    vectors = world["vectors"]
    settings = world["settings"]
    session = world["session"]

    photo_bytes = b"fake-photo-bytes-for-alice"
    pcm = _make_pcm(seconds=1.0)

    enrollment = EnrollmentService(
        store,
        vectors,
        FakeFaceEmbedder(),
        FakeVoiceEmbedder(),
    )
    person = enrollment.enroll_person("Alice Nguyen", photo_bytes, pcm)

    text = "alice confirms the launch date"
    raw = RawSegment(
        text=text,
        avg_logprob=-0.2,
        no_speech_prob=0.05,
        start=0.0,
        end=1.0,
    )
    transcriber = FakeTranscriber(script=[[raw]])
    pipeline, _text_embedder = _make_pipeline(
        store, vectors, settings, transcriber
    )

    sealed = SealedSegment(seq=4, pcm=pcm, duration_s=1.0)
    rows = pipeline.process_segment(
        session["id"], sealed, photo_bytes=photo_bytes
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["gate_status"] == GATE_VERIFIED
    assert row["speaker_person_id"] == person["id"]
    assert row["speaker_label"] == person["name"]

    stored = store.get_segment(row["id"])
    assert stored is not None
    assert stored["speaker_person_id"] == person["id"]


def test_base_offset_shifts_stored_start_and_end_offsets(world):
    session = world["session"]

    raw = RawSegment(
        text="offset check segment text",
        avg_logprob=-0.2,
        no_speech_prob=0.05,
        start=0.25,
        end=1.25,
    )
    transcriber = FakeTranscriber(script=[[raw]])
    pipeline, _text_embedder = _make_pipeline(
        world["store"], world["vectors"], world["settings"], transcriber
    )

    pcm = _make_pcm(seconds=1.5)
    sealed = SealedSegment(seq=5, pcm=pcm, duration_s=1.5)
    rows = pipeline.process_segment(session["id"], sealed, base_offset_s=42.0)

    assert len(rows) == 1
    row = rows[0]
    assert row["start_offset_s"] == pytest.approx(42.25)
    assert row["end_offset_s"] == pytest.approx(43.25)

    stored = world["store"].get_segment(row["id"])
    assert stored is not None
    assert stored["start_offset_s"] == pytest.approx(42.25)
    assert stored["end_offset_s"] == pytest.approx(43.25)
