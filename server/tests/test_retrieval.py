"""Tests for the OnRecord retrieval contract in onrecord.query.

Fixtures are built locally in this file: a real Store on a tmp data
directory, a FakeVectorStore, and a HashingTextEmbedder. Segments are
seeded by calling store methods plus manual chunk upserts, mirroring
how onrecord.seed does it. No conftest.py is used.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from onrecord.chroma_store import FakeVectorStore
from onrecord.config import Settings
from onrecord.db import Store
from onrecord.embeddings import HashingTextEmbedder
from onrecord.interfaces import (
    ATTR_MANUAL,
    COLLECTION_CHUNKS,
    GATE_REVIEW,
    GATE_VERIFIED,
    REASON_NO_PEOPLE,
    REASON_NO_VERIFIED,
    REASON_PERSON_NOT_FOUND,
)
from onrecord.query import QueryService


BUDGET_TEXT = (
    "Let us lock the budget for the event. The venue budget is 2400 "
    "dollars total, and we cannot go over that."
)


class Env:
    """One isolated retrieval stack for a single test."""

    def __init__(self, tmp_path: Path):
        self.settings = Settings(
            data_dir=tmp_path,
            chroma_dir=tmp_path / "chroma",
        )
        self.store = Store(self.settings)
        self.vectors = FakeVectorStore()
        self.embedder = HashingTextEmbedder()
        self.service = QueryService(self.store, self.vectors, self.embedder)

    def close(self):
        self.store.close()


@pytest.fixture()
def env(tmp_path):
    built = Env(tmp_path)
    yield built
    built.close()


def add_person(env: Env, name: str) -> dict:
    person = env.store.create_person(name, "")
    assert person is not None and person.get("id")
    return person


def add_session(env: Env, title: str = "Budget planning chat") -> dict:
    session = env.store.create_session(title, "phone", True)
    assert session is not None and session.get("id")
    return session


def add_segment(
    env: Env,
    session_id: str,
    seq: int,
    person: dict,
    text: str,
    gate_status: str = GATE_VERIFIED,
    start_offset_s: float = 0.0,
    end_offset_s: float = 4.0,
    upsert_chunk: bool = True,
    raw_text: str = None,
):
    """Insert a segment via the store and optionally upsert its chunk.

    Mirrors seed.py: add_segment, set_segment_speaker with MANUAL, then
    a transcript chunk upsert carrying segment_id in its metadata.
    Returns the stored segment dict.
    """
    stored_raw = raw_text if raw_text is not None else text
    segment = env.store.add_segment(
        session_id,
        seq,
        text,
        stored_raw,
        -0.19,
        0.01,
        gate_status,
        0,
        start_offset_s,
        end_offset_s,
    )
    assert segment is not None and segment.get("id")
    segment_id = segment["id"]

    if person is not None:
        env.store.set_segment_speaker(
            segment_id,
            person["id"],
            person["name"],
            ATTR_MANUAL,
            None,
            None,
        )
        segment = env.store.get_segment(segment_id)

    if upsert_chunk:
        metadata = {
            "segment_id": segment_id,
            "session_id": session_id,
            "person_id": person["id"] if person is not None else None,
            "speaker_name": person["name"] if person is not None else "Unknown",
            "gate_status": gate_status,
        }
        env.vectors.upsert(
            COLLECTION_CHUNKS,
            str(segment_id),
            env.embedder.embed(text),
            metadata,
            text,
        )
    return segment


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


# ----------------------------------------------------------------------
# Empty store
# ----------------------------------------------------------------------


def test_search_conversations_empty_store_returns_no_people(env):
    result = env.service.search_conversations("budget")

    assert result.status == "EMPTY"
    assert result.reason == REASON_NO_PEOPLE
    assert result.reason == "NO_PEOPLE"
    assert result.results == []


# ----------------------------------------------------------------------
# Semantic search: verbatim text and session offset timestamps
# ----------------------------------------------------------------------


def test_search_budget_returns_verbatim_hit_with_offset_start_iso(env):
    maya = add_person(env, "Maya Chen")
    session = add_session(env)
    start_offset_s = 8.0
    end_offset_s = 12.0
    segment = add_segment(
        env,
        session["id"],
        0,
        maya,
        BUDGET_TEXT,
        gate_status=GATE_VERIFIED,
        start_offset_s=start_offset_s,
        end_offset_s=end_offset_s,
    )
    add_segment(
        env,
        session["id"],
        1,
        maya,
        "Catering is capped at 900 dollars, and the rest of the money goes to prizes.",
        gate_status=GATE_VERIFIED,
        start_offset_s=12.0,
        end_offset_s=16.0,
    )

    result = env.service.search_conversations("what did Maya say about the budget")

    assert result.status == "OK"
    assert result.reason is None
    assert len(result.results) >= 1

    hits_by_id = {hit.segment_id: hit for hit in result.results}
    assert segment["id"] in hits_by_id
    hit = hits_by_id[segment["id"]]

    # Verbatim: the hit text is exactly the stored segment text.
    stored = env.store.get_segment(segment["id"])
    assert hit.text == BUDGET_TEXT
    assert hit.text == stored["text"]

    # start_iso is session started_at plus the segment offset, checked
    # by parsing both ISO strings rather than comparing raw strings.
    session_started = parse_iso(session["started_at"])
    assert parse_iso(hit.start_iso) == session_started + timedelta(
        seconds=start_offset_s
    )
    assert parse_iso(hit.end_iso) == session_started + timedelta(
        seconds=end_offset_s
    )


# ----------------------------------------------------------------------
# Person filtering, unknown names, ambiguous names
# ----------------------------------------------------------------------


def test_search_person_filter_returns_only_other_person_segments(env):
    maya = add_person(env, "Maya Chen")
    liam = add_person(env, "Liam Park")
    session = add_session(env)
    maya_segment = add_segment(
        env,
        session["id"],
        0,
        maya,
        "Maya says the budget for the venue is 2400 dollars total.",
        start_offset_s=0.0,
        end_offset_s=4.0,
    )
    liam_segment = add_segment(
        env,
        session["id"],
        1,
        liam,
        "Liam asks how much of that budget should we plan for catering.",
        start_offset_s=4.0,
        end_offset_s=8.0,
    )

    result = env.service.search_conversations("budget", person_ref=liam["name"])

    assert result.status == "OK"
    assert len(result.results) >= 1
    result_ids = {hit.segment_id for hit in result.results}
    assert liam_segment["id"] in result_ids
    assert maya_segment["id"] not in result_ids
    for hit in result.results:
        assert hit.speaker_person_id == liam["id"]
        assert hit.speaker_name == "Liam Park"


def test_search_unknown_person_returns_person_not_found(env):
    maya = add_person(env, "Maya Chen")
    session = add_session(env)
    add_segment(env, session["id"], 0, maya, BUDGET_TEXT)

    result = env.service.search_conversations(
        "budget", person_ref="No Such Person"
    )

    assert result.status == "EMPTY"
    assert result.reason == REASON_PERSON_NOT_FOUND
    assert result.reason == "PERSON_NOT_FOUND"
    assert result.results == []


def test_search_two_people_same_name_returns_ambiguous_name(env):
    first = add_person(env, "Alex Rivera")
    second = add_person(env, "Alex Rivera")
    assert first["id"] != second["id"]
    session = add_session(env)
    add_segment(env, session["id"], 0, first, BUDGET_TEXT)

    result = env.service.search_conversations("budget", person_ref="Alex Rivera")

    assert result.status == "EMPTY"
    assert result.reason == "AMBIGUOUS_NAME"
    assert result.results == []


# ----------------------------------------------------------------------
# VERIFIED only invariant
# ----------------------------------------------------------------------


def test_search_filters_out_review_segment_even_when_chunk_upserted(env):
    maya = add_person(env, "Maya Chen")
    session = add_session(env)
    verified = add_segment(
        env,
        session["id"],
        0,
        maya,
        "The verified budget note says catering is capped at 900 dollars.",
        gate_status=GATE_VERIFIED,
        start_offset_s=0.0,
        end_offset_s=4.0,
    )
    review = add_segment(
        env,
        session["id"],
        1,
        maya,
        "The review budget note claims catering is capped at 5000 dollars.",
        gate_status=GATE_REVIEW,
        start_offset_s=4.0,
        end_offset_s=8.0,
        upsert_chunk=True,
    )
    # Guard the premise: the REVIEW chunk really is in the vector store.
    assert env.vectors.get(COLLECTION_CHUNKS, str(review["id"])) is not None

    result = env.service.search_conversations("budget catering")

    assert result.status == "OK"
    result_ids = {hit.segment_id for hit in result.results}
    assert verified["id"] in result_ids
    assert review["id"] not in result_ids
    for hit in result.results:
        assert "5000 dollars" not in hit.text


def test_search_person_with_no_verified_segments_returns_no_verified(env):
    rita = add_person(env, "Rita Review")
    session = add_session(env)
    add_segment(
        env,
        session["id"],
        0,
        rita,
        "Rita talks about the budget but this segment is still in review.",
        gate_status=GATE_REVIEW,
        start_offset_s=0.0,
        end_offset_s=4.0,
        upsert_chunk=True,
    )
    add_segment(
        env,
        session["id"],
        1,
        rita,
        "Rita mentions the catering budget again, also still in review.",
        gate_status=GATE_REVIEW,
        start_offset_s=4.0,
        end_offset_s=8.0,
        upsert_chunk=True,
    )

    result = env.service.search_conversations("budget", person_ref=rita["name"])

    assert result.status == "EMPTY"
    assert result.reason == REASON_NO_VERIFIED
    assert result.reason == "NO_VERIFIED_SEGMENTS"
    assert result.results == []


# ----------------------------------------------------------------------
# Exact search
# ----------------------------------------------------------------------


def test_exact_search_finds_verified_by_substring_and_skips_review(env):
    maya = add_person(env, "Maya Chen")
    session = add_session(env)
    distinctive = "quixotic budget ledger"
    verified = add_segment(
        env,
        session["id"],
        0,
        maya,
        "The quixotic budget ledger mentions a zebra umbrella purchase.",
        gate_status=GATE_VERIFIED,
        start_offset_s=0.0,
        end_offset_s=4.0,
        upsert_chunk=False,
    )
    review = add_segment(
        env,
        session["id"],
        1,
        maya,
        "The quixotic budget ledger draft is an unapproved review note.",
        gate_status=GATE_REVIEW,
        start_offset_s=4.0,
        end_offset_s=8.0,
        upsert_chunk=False,
    )

    result = env.service.exact_search(distinctive)

    assert result.status == "OK"
    assert result.reason is None
    result_ids = {hit.segment_id for hit in result.results}
    assert verified["id"] in result_ids
    assert review["id"] not in result_ids
    for hit in result.results:
        assert hit.score is None
        assert distinctive in hit.text


# ----------------------------------------------------------------------
# Interaction detail view
# ----------------------------------------------------------------------


def test_get_interaction_orders_by_seq_and_strips_raw_text(env):
    maya = add_person(env, "Maya Chen")
    session = add_session(env)
    # Insert out of seq order on purpose; the view must sort by seq.
    add_segment(
        env,
        session["id"],
        2,
        maya,
        "Third budget line, spoken last.",
        start_offset_s=8.0,
        end_offset_s=12.0,
        raw_text="RAW SECRET third line",
    )
    add_segment(
        env,
        session["id"],
        0,
        maya,
        "First budget line, spoken first.",
        start_offset_s=0.0,
        end_offset_s=4.0,
        raw_text="RAW SECRET first line",
    )
    add_segment(
        env,
        session["id"],
        1,
        maya,
        "Second budget line, spoken in the middle.",
        start_offset_s=4.0,
        end_offset_s=8.0,
        raw_text="RAW SECRET second line",
    )

    interaction = env.service.get_interaction(session["id"])

    assert interaction is not None
    assert interaction["id"] == session["id"]
    segments = interaction["segments"]
    assert [seg["seq"] for seg in segments] == [0, 1, 2]
    for seg in segments:
        assert "raw_text" not in seg


# ----------------------------------------------------------------------
# Person profile
# ----------------------------------------------------------------------


def test_get_person_profile_counts_only_verified_talk_time(env):
    maya = add_person(env, "Maya Chen")
    session = add_session(env)
    add_segment(
        env,
        session["id"],
        0,
        maya,
        "Maya opens the budget discussion.",
        gate_status=GATE_VERIFIED,
        start_offset_s=0.0,
        end_offset_s=4.0,
    )
    add_segment(
        env,
        session["id"],
        1,
        maya,
        "Maya explains the catering budget cap in detail.",
        gate_status=GATE_VERIFIED,
        start_offset_s=10.0,
        end_offset_s=16.0,
    )
    add_segment(
        env,
        session["id"],
        2,
        maya,
        "Maya says something long that never passed the gate.",
        gate_status=GATE_REVIEW,
        start_offset_s=20.0,
        end_offset_s=120.0,
    )

    profile = env.service.get_person_profile(maya["name"])

    assert profile is not None
    assert profile["id"] == maya["id"]
    assert profile["verified_segments"] == 2
    # Verified durations only: 4.0 plus 6.0. The 100 second REVIEW
    # segment must not count.
    assert profile["talk_seconds"] == 10.0
    assert profile["talk_seconds"] == (4.0 - 0.0) + (16.0 - 10.0)


def test_get_person_profile_unknown_person_returns_none(env):
    add_person(env, "Maya Chen")

    assert env.service.get_person_profile("No Such Person") is None
