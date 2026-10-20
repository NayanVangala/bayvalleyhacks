"""Shared fixtures and test doubles for the OnRecord server tests.

Everything here is deterministic and offline: a real Store on a
temporary SQLite file, the build's FakeVectorStore, and the build's
HashingTextEmbedder. No ML models, no network, no wall clock reads
beyond the store's own timestamps.

The settings fixture is hermetic: DATA_DIR and CHROMA_DIR point at
per-test tmp subdirectories, DATABASE_URL is blank (treated as unset,
so the store uses the SQLite file under DATA_DIR), ONRECORD_VECTOR is
forced to fake, and the config cache is reset before and after each
test so overrides never leak between tests. This module only imports
from onrecord.config, onrecord.db, onrecord.chroma_store and
onrecord.embeddings, so it stays importable without fastapi.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `import onrecord` work no matter where pytest is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onrecord.chroma_store import FakeVectorStore  # noqa: E402
from onrecord.config import (  # noqa: E402
    Settings,
    get_settings,
    reset_settings_cache,
)
from onrecord.db import Store  # noqa: E402
from onrecord.embeddings import HashingTextEmbedder  # noqa: E402
from onrecord.interfaces import VectorHit  # noqa: E402


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    """Settings from the environment, rooted at tmp subdirectories.

    Sets DATA_DIR and CHROMA_DIR to fresh subdirectories of tmp_path,
    DATABASE_URL to empty and ONRECORD_VECTOR to fake, resets the
    config cache, and returns get_settings(). The cache is reset again
    on teardown so the next test re-reads its own environment.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ONRECORD_VECTOR", "fake")
    reset_settings_cache()
    try:
        yield get_settings()
    finally:
        reset_settings_cache()


@pytest.fixture()
def store(settings):
    instance = Store(settings)
    instance.init_schema()
    yield instance
    instance.close()


@pytest.fixture()
def vectors():
    return FakeVectorStore()


@pytest.fixture()
def text_embedder():
    return HashingTextEmbedder()


class StubVectorStore:
    """VectorStore double that returns prescribed hits per collection.

    Fusion thresholds are about scores, so tests that need an exact
    score (a boundary value like 0.45) prescribe the hit instead of
    doing cosine geometry to land on it.
    """

    def __init__(self, hits_by_collection=None):
        self._hits = dict(hits_by_collection or {})
        self.upserts = []

    def upsert(self, collection, id, embedding, metadata, document=None):
        self.upserts.append((collection, id, list(embedding), dict(metadata), document))

    def query(self, collection, embedding, n=5):
        return list(self._hits.get(collection, []))[:n]

    def get(self, collection, id):
        for hit in self._hits.get(collection, []):
            if hit.id == id:
                return hit
        return None

    def delete(self, collection, id):
        self._hits[collection] = [
            hit for hit in self._hits.get(collection, []) if hit.id != id
        ]

    def count(self, collection):
        return len(self._hits.get(collection, []))


def hit(person_id, score, kind="face"):
    """A VectorHit naming person_id with an exact score."""
    return VectorHit(
        id="%s:%s" % (kind, person_id),
        score=score,
        metadata={"person_id": person_id},
    )


class FixedEmbedder:
    """Embedder double returning a fixed vector, or None when told to."""

    dim = 4

    def __init__(self, vector=(1.0, 0.0, 0.0, 0.0), fail=False):
        self._vector = list(vector) if vector is not None else None
        self._fail = fail

    def embed_image(self, image_bytes):
        if self._fail or not image_bytes:
            return None
        return list(self._vector)

    def embed_pcm(self, pcm, sample_rate=16000):
        if self._fail or not pcm:
            return None
        return list(self._vector)


def add_segment(store, session_id, seq, text, gate_status, speaker=None,
                start=0.0, end=5.0, raw_text=None):
    """Insert one segment, optionally attributed, and return its row."""
    row = store.add_segment(
        session_id,
        seq,
        text,
        raw_text=raw_text if raw_text is not None else text,
        gate_status=gate_status,
        start_offset_s=start,
        end_offset_s=end,
    )
    if speaker is not None:
        row = store.set_segment_speaker(
            row["id"], speaker["id"], speaker["name"], "FUSION_FACE_VOICE",
            face_score=0.9, voice_score=0.9,
        )
    return row
