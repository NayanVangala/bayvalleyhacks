"""Tests for the relational Store and the FakeVectorStore.

Covers onrecord.db.Store end to end on a tmp SQLite file (people,
sessions, segments, exact search, corrections, Slack outbox) and the
in-memory FakeVectorStore from onrecord.chroma_store (cosine query
ordering, get, delete, count, unknown collections). All state comes
from the conftest fixtures, so every test is hermetic: tmp dirs only,
no network, no ML dependencies.
"""

from __future__ import annotations

import time

import pytest

from onrecord.interfaces import (
    COLLECTION_CHUNKS,
    COLLECTION_FACE,
    COLLECTION_VOICE,
    COLLECTIONS,
)


def _tick() -> None:
    """Sleep briefly so consecutive store timestamps differ.

    Several Store orderings are by created_at or started_at, and two
    rows written inside the same microsecond would tie. A 10 ms pause
    between writes keeps the intended order deterministic.
    """
    time.sleep(0.01)


def _person(store, name="Alice Smith", notes=""):
    return store.create_person(name, notes=notes)


def _session(store, title="Standup"):
    return store.create_session(title=title)


def _segment(store, session_id, seq=0, text="hello world", **kwargs):
    return store.add_segment(session_id, seq, text, **kwargs)


# ----------------------------------------------------------------------
# Fixture sanity
# ----------------------------------------------------------------------


def test_settings_fixture_is_hermetic(settings, tmp_path):
    assert settings.data_dir == tmp_path / "data"
    assert settings.chroma_dir == tmp_path / "chroma"
    assert not settings.database_url
    assert settings.db_path == tmp_path / "data" / "onrecord.db"


def test_store_starts_empty(store):
    assert store.people_count() == 0
    assert store.list_people() == []
    assert store.list_sessions() == []
    assert store.list_outbox() == []
    assert store.list_corrections() == []


def test_store_db_file_lives_under_tmp_data_dir(store, settings):
    store.create_person("File Check")
    assert settings.db_path.exists()


# ----------------------------------------------------------------------
# People
# ----------------------------------------------------------------------


def test_create_person_returns_full_row_with_defaults(store):
    person = store.create_person("Alice Smith")
    assert person["id"]
    assert person["name"] == "Alice Smith"
    assert person["notes"] == ""
    assert person["created_at"]
    assert person["updated_at"] == person["created_at"]


def test_create_person_with_notes(store):
    person = store.create_person("Bob Jones", notes="met at the demo")
    assert person["notes"] == "met at the demo"


def test_get_person_roundtrip(store):
    created = _person(store, "Carol Danvers", notes="pilot")
    fetched = store.get_person(created["id"])
    assert fetched == created


def test_get_person_missing_returns_none(store):
    assert store.get_person("no-such-id") is None


def test_list_people_in_creation_order(store):
    assert store.list_people() == []
    first = _person(store, "First Person")
    _tick()
    second = _person(store, "Second Person")
    people = store.list_people()
    assert [p["id"] for p in people] == [first["id"], second["id"]]
    assert [p["name"] for p in people] == ["First Person", "Second Person"]


def test_find_people_by_name_is_case_insensitive(store):
    created = _person(store, "Alice Smith")
    _person(store, "Someone Else")
    for query in ("Alice Smith", "alice smith", "ALICE SMITH", "aLiCe sMiTh"):
        found = store.find_people_by_name(query)
        assert [p["id"] for p in found] == [created["id"]]


def test_find_people_by_name_is_exact_not_substring(store):
    _person(store, "Alice Smith")
    assert store.find_people_by_name("Alice") == []
    assert store.find_people_by_name("Smith") == []
    assert store.find_people_by_name("Alice Smith Junior") == []


def test_find_people_by_name_no_match_returns_empty(store):
    _person(store, "Alice Smith")
    assert store.find_people_by_name("Nobody Here") == []


def test_find_people_by_name_returns_all_duplicates(store):
    one = _person(store, "Sam Lee")
    _tick()
    two = _person(store, "sam lee")
    found = store.find_people_by_name("SAM LEE")
    assert [p["id"] for p in found] == [one["id"], two["id"]]


def test_update_person_name_only_keeps_notes(store):
    person = _person(store, "Old Name", notes="keep me")
    _tick()
    updated = store.update_person(person["id"], name="New Name")
    assert updated["name"] == "New Name"
    assert updated["notes"] == "keep me"
    assert updated["id"] == person["id"]
    assert updated["updated_at"] > person["updated_at"]
    assert store.get_person(person["id"])["name"] == "New Name"


def test_update_person_notes_only_keeps_name(store):
    person = _person(store, "Steady Name", notes="old notes")
    updated = store.update_person(person["id"], notes="new notes")
    assert updated["name"] == "Steady Name"
    assert updated["notes"] == "new notes"


def test_update_person_name_and_notes(store):
    person = _person(store, "Before", notes="before notes")
    updated = store.update_person(
        person["id"], name="After", notes="after notes"
    )
    assert updated["name"] == "After"
    assert updated["notes"] == "after notes"


def test_update_person_missing_returns_none(store):
    assert store.update_person("no-such-id", name="Ghost") is None


def test_people_count(store):
    assert store.people_count() == 0
    _person(store, "One")
    assert store.people_count() == 1
    _person(store, "Two")
    _person(store, "Three")
    assert store.people_count() == 3


def test_people_count_unaffected_by_update(store):
    person = _person(store, "Counted Once")
    store.update_person(person["id"], notes="edited")
    assert store.people_count() == 1


# ----------------------------------------------------------------------
# Sessions
# ----------------------------------------------------------------------


def test_create_session_defaults(store):
    session = store.create_session()
    assert session["id"]
    assert session["title"] == ""
    assert session["source"] == "glasses"
    assert session["status"] == "recording"
    assert session["rehearsal"] is False
    assert session["started_at"]
    assert session["ended_at"] is None


def test_create_session_explicit_values(store):
    session = store.create_session(
        title="Investor call", source="phone", rehearsal=True
    )
    assert session["title"] == "Investor call"
    assert session["source"] == "phone"
    assert session["rehearsal"] is True
    assert session["status"] == "recording"


def test_get_session_roundtrip_and_missing(store):
    created = _session(store, "Roundtrip")
    assert store.get_session(created["id"]) == created
    assert store.get_session("no-such-id") is None


def test_list_sessions_newest_first(store):
    first = _session(store, "first")
    _tick()
    second = _session(store, "second")
    _tick()
    third = _session(store, "third")
    sessions = store.list_sessions()
    assert [s["id"] for s in sessions] == [
        third["id"],
        second["id"],
        first["id"],
    ]
    started = [s["started_at"] for s in sessions]
    assert started == sorted(started, reverse=True)


def test_set_session_status(store):
    session = _session(store)
    updated = store.set_session_status(session["id"], "processing")
    assert updated["status"] == "processing"
    assert store.get_session(session["id"])["status"] == "processing"


def test_set_session_status_missing_returns_none(store):
    assert store.set_session_status("no-such-id", "processing") is None


def test_end_session_sets_ready_and_ended_at(store):
    session = _session(store)
    _tick()
    ended = store.end_session(session["id"])
    assert ended["status"] == "ready"
    assert ended["ended_at"] is not None
    assert ended["ended_at"] >= ended["started_at"]
    assert store.get_session(session["id"])["status"] == "ready"


def test_end_session_missing_returns_none(store):
    assert store.end_session("no-such-id") is None


# ----------------------------------------------------------------------
# Segments
# ----------------------------------------------------------------------


def test_add_segment_defaults_are_unknown_and_unattributed(store):
    session = _session(store)
    segment = store.add_segment(session["id"], 0, "hello world")
    assert segment["id"]
    assert segment["session_id"] == session["id"]
    assert segment["seq"] == 0
    assert segment["text"] == "hello world"
    assert segment["speaker_person_id"] is None
    assert segment["speaker_label"] == "Unknown"
    assert segment["attribution_source"] == "UNKNOWN"
    assert segment["face_score"] is None
    assert segment["voice_score"] is None
    assert segment["raw_text"] is None
    assert segment["avg_logprob"] is None
    assert segment["no_speech_prob"] is None
    assert segment["gate_status"] == "REVIEW"
    assert segment["uncertain"] is False
    assert segment["start_offset_s"] == 0
    assert segment["end_offset_s"] == 0
    assert segment["audio_path"] is None
    assert segment["created_at"]


def test_add_segment_explicit_fields_roundtrip(store):
    session = _session(store)
    segment = store.add_segment(
        session["id"],
        3,
        "clean text",
        raw_text="clean text um",
        avg_logprob=-0.25,
        no_speech_prob=0.05,
        gate_status="VERIFIED",
        uncertain=True,
        start_offset_s=1.5,
        end_offset_s=4.25,
        audio_path="/tmp/seg3.wav",
    )
    assert segment["seq"] == 3
    assert segment["raw_text"] == "clean text um"
    assert segment["avg_logprob"] == pytest.approx(-0.25)
    assert segment["no_speech_prob"] == pytest.approx(0.05)
    assert segment["gate_status"] == "VERIFIED"
    assert segment["uncertain"] is True
    assert segment["start_offset_s"] == pytest.approx(1.5)
    assert segment["end_offset_s"] == pytest.approx(4.25)
    assert segment["audio_path"] == "/tmp/seg3.wav"
    assert store.get_segment(segment["id"]) == segment


def test_get_segment_missing_returns_none(store):
    assert store.get_segment("no-such-id") is None


def test_set_segment_speaker_sets_attribution(store):
    session = _session(store)
    person = _person(store, "Dana")
    segment = _segment(store, session["id"], text="attributed line")
    updated = store.set_segment_speaker(
        segment["id"],
        person["id"],
        "Dana",
        "FUSION_FACE_VOICE",
        face_score=0.9,
        voice_score=0.8,
    )
    assert updated["speaker_person_id"] == person["id"]
    assert updated["speaker_label"] == "Dana"
    assert updated["attribution_source"] == "FUSION_FACE_VOICE"
    assert updated["face_score"] == pytest.approx(0.9)
    assert updated["voice_score"] == pytest.approx(0.8)
    assert store.get_segment(segment["id"])["speaker_label"] == "Dana"


def test_set_segment_speaker_without_scores_keeps_them_none(store):
    session = _session(store)
    person = _person(store, "Erin")
    segment = _segment(store, session["id"])
    updated = store.set_segment_speaker(
        segment["id"], person["id"], "Erin", "MANUAL"
    )
    assert updated["speaker_person_id"] == person["id"]
    assert updated["attribution_source"] == "MANUAL"
    assert updated["face_score"] is None
    assert updated["voice_score"] is None


def test_set_segment_speaker_can_clear_to_unknown(store):
    session = _session(store)
    person = _person(store, "Frank")
    segment = _segment(store, session["id"])
    store.set_segment_speaker(segment["id"], person["id"], "Frank", "MANUAL")
    cleared = store.set_segment_speaker(
        segment["id"], None, "Unknown", "UNKNOWN"
    )
    assert cleared["speaker_person_id"] is None
    assert cleared["speaker_label"] == "Unknown"
    assert cleared["attribution_source"] == "UNKNOWN"


def test_list_segments_ordered_by_seq(store):
    session = _session(store)
    other = _session(store, "other session")
    seg2 = _segment(store, session["id"], seq=2, text="third")
    seg0 = _segment(store, session["id"], seq=0, text="first")
    seg1 = _segment(store, session["id"], seq=1, text="second")
    _segment(store, other["id"], seq=0, text="belongs elsewhere")
    segments = store.list_segments(session["id"])
    assert [s["seq"] for s in segments] == [0, 1, 2]
    assert [s["id"] for s in segments] == [seg0["id"], seg1["id"], seg2["id"]]
    assert [s["text"] for s in segments] == ["first", "second", "third"]


def test_list_segments_empty_session(store):
    session = _session(store)
    assert store.list_segments(session["id"]) == []


def _attributed_segments(store):
    """Two people, three attributed segments, one unattributed."""
    alice = _person(store, "Alice")
    bob = _person(store, "Bob")
    session = _session(store)
    other_session = _session(store, "second session")

    alice_verified = _segment(
        store, session["id"], seq=0, text="alice verified",
        gate_status="VERIFIED",
    )
    store.set_segment_speaker(
        alice_verified["id"], alice["id"], "Alice", "MANUAL"
    )
    _tick()
    alice_review = _segment(
        store, other_session["id"], seq=0, text="alice review",
        gate_status="REVIEW",
    )
    store.set_segment_speaker(
        alice_review["id"], alice["id"], "Alice", "MANUAL"
    )
    _tick()
    bob_verified = _segment(
        store, session["id"], seq=1, text="bob verified",
        gate_status="VERIFIED",
    )
    store.set_segment_speaker(
        bob_verified["id"], bob["id"], "Bob", "MANUAL"
    )
    _segment(store, session["id"], seq=2, text="nobody yet")
    return alice, bob, alice_verified, alice_review, bob_verified


def test_segments_for_person_without_gate_filter(store):
    alice, bob, alice_verified, alice_review, bob_verified = (
        _attributed_segments(store)
    )
    rows = store.segments_for_person(alice["id"])
    assert [r["id"] for r in rows] == [
        alice_verified["id"],
        alice_review["id"],
    ]
    rows_bob = store.segments_for_person(bob["id"])
    assert [r["id"] for r in rows_bob] == [bob_verified["id"]]


def test_segments_for_person_with_gate_filter(store):
    alice, _bob, alice_verified, alice_review, _bv = _attributed_segments(
        store
    )
    verified = store.segments_for_person(alice["id"], gate_status="VERIFIED")
    assert [r["id"] for r in verified] == [alice_verified["id"]]
    review = store.segments_for_person(alice["id"], gate_status="REVIEW")
    assert [r["id"] for r in review] == [alice_review["id"]]
    assert store.segments_for_person(alice["id"], gate_status="SILENCE") == []


def test_segments_for_person_unknown_person_returns_empty(store):
    _attributed_segments(store)
    assert store.segments_for_person("no-such-person") == []
    assert store.segments_for_person("no-such-person", "VERIFIED") == []


# ----------------------------------------------------------------------
# Exact segment search
# ----------------------------------------------------------------------


def test_search_segments_exact_matches_substring(store):
    session = _session(store)
    hit = _segment(store, session["id"], seq=0, text="the budget is approved")
    _segment(store, session["id"], seq=1, text="lunch plans instead")
    results = store.search_segments_exact("budget")
    assert [r["id"] for r in results] == [hit["id"]]


def test_search_segments_exact_is_case_insensitive(store):
    session = _session(store)
    _segment(store, session["id"], seq=0, text="Hello World from Dana")
    for term in ("hello", "HELLO", "HeLLo WoRLD", "DANA"):
        results = store.search_segments_exact(term)
        assert len(results) == 1, term
        assert results[0]["text"] == "Hello World from Dana"


def test_search_segments_exact_no_match_returns_empty(store):
    session = _session(store)
    _segment(store, session["id"], seq=0, text="something stored")
    assert store.search_segments_exact("absent phrase") == []


def test_search_segments_exact_percent_is_literal(store):
    session = _session(store)
    literal = _segment(
        store, session["id"], seq=0, text="we hit 100% coverage"
    )
    _segment(store, session["id"], seq=1, text="we hit 1000 coverage")
    _segment(store, session["id"], seq=2, text="we hit 100 percent coverage")
    results = store.search_segments_exact("100%")
    assert [r["id"] for r in results] == [literal["id"]]
    only_percent = store.search_segments_exact("%")
    assert [r["id"] for r in only_percent] == [literal["id"]]


def test_search_segments_exact_underscore_is_literal(store):
    session = _session(store)
    literal = _segment(store, session["id"], seq=0, text="the a_b test")
    _segment(store, session["id"], seq=1, text="the acb test")
    _segment(store, session["id"], seq=2, text="the axb test")
    results = store.search_segments_exact("a_b")
    assert [r["id"] for r in results] == [literal["id"]]
    only_underscore = store.search_segments_exact("_")
    assert [r["id"] for r in only_underscore] == [literal["id"]]


def test_search_segments_exact_backslash_is_literal(store):
    session = _session(store)
    literal = _segment(store, session["id"], seq=0, text=r"open C:\temp now")
    _segment(store, session["id"], seq=1, text="open C:temp now")
    results = store.search_segments_exact(r"C:\temp")
    assert [r["id"] for r in results] == [literal["id"]]


def test_search_segments_exact_newest_first_and_limit(store):
    session = _session(store)
    first = _segment(store, session["id"], seq=0, text="token one")
    _tick()
    second = _segment(store, session["id"], seq=1, text="token two")
    _tick()
    third = _segment(store, session["id"], seq=2, text="token three")
    results = store.search_segments_exact("token")
    assert [r["id"] for r in results] == [
        third["id"],
        second["id"],
        first["id"],
    ]
    limited = store.search_segments_exact("token", limit=2)
    assert [r["id"] for r in limited] == [third["id"], second["id"]]


# ----------------------------------------------------------------------
# Corrections
# ----------------------------------------------------------------------


def _correction_setup(store):
    alice = _person(store, "Alice")
    bob = _person(store, "Bob")
    session = _session(store)
    seg_one = _segment(store, session["id"], seq=0, text="first line")
    seg_two = _segment(store, session["id"], seq=1, text="second line")
    return alice, bob, seg_one, seg_two


def test_add_correction_defaults_to_wearer(store):
    alice, bob, seg_one, _seg_two = _correction_setup(store)
    correction = store.add_correction(seg_one["id"], alice["id"], bob["id"])
    assert correction["id"]
    assert correction["segment_id"] == seg_one["id"]
    assert correction["old_speaker_person_id"] == alice["id"]
    assert correction["new_speaker_person_id"] == bob["id"]
    assert correction["operator_id"] == "wearer"
    assert correction["created_at"]


def test_add_correction_explicit_operator_and_none_old_speaker(store):
    _alice, bob, seg_one, _seg_two = _correction_setup(store)
    correction = store.add_correction(
        seg_one["id"], None, bob["id"], operator_id="operator-7"
    )
    assert correction["old_speaker_person_id"] is None
    assert correction["new_speaker_person_id"] == bob["id"]
    assert correction["operator_id"] == "operator-7"


def test_list_corrections_all_in_creation_order(store):
    alice, bob, seg_one, seg_two = _correction_setup(store)
    first = store.add_correction(seg_one["id"], alice["id"], bob["id"])
    _tick()
    second = store.add_correction(seg_two["id"], bob["id"], alice["id"])
    _tick()
    third = store.add_correction(seg_one["id"], bob["id"], alice["id"])
    corrections = store.list_corrections()
    assert [c["id"] for c in corrections] == [
        first["id"],
        second["id"],
        third["id"],
    ]


def test_list_corrections_filtered_by_segment(store):
    alice, bob, seg_one, seg_two = _correction_setup(store)
    on_one = store.add_correction(seg_one["id"], alice["id"], bob["id"])
    _tick()
    store.add_correction(seg_two["id"], bob["id"], alice["id"])
    _tick()
    also_on_one = store.add_correction(seg_one["id"], bob["id"], alice["id"])
    filtered = store.list_corrections(seg_one["id"])
    assert [c["id"] for c in filtered] == [on_one["id"], also_on_one["id"]]
    assert store.list_corrections("no-such-segment") == []


def test_list_corrections_empty(store):
    assert store.list_corrections() == []


# ----------------------------------------------------------------------
# Slack outbox
# ----------------------------------------------------------------------


def test_enqueue_slack_defaults_to_queued(store):
    session = _session(store)
    row = store.enqueue_slack(session["id"], "digest body here")
    assert row["id"]
    assert row["session_id"] == session["id"]
    assert row["body"] == "digest body here"
    assert row["status"] == "queued"
    assert row["posted_at"] is None
    assert row["created_at"]


def test_list_outbox_all_and_filtered_by_status(store):
    session = _session(store)
    first = store.enqueue_slack(session["id"], "first body")
    _tick()
    second = store.enqueue_slack(session["id"], "second body")
    store.mark_outbox_posted(first["id"])
    everything = store.list_outbox()
    assert [r["id"] for r in everything] == [first["id"], second["id"]]
    queued = store.list_outbox(status="queued")
    assert [r["id"] for r in queued] == [second["id"]]
    posted = store.list_outbox(status="posted")
    assert [r["id"] for r in posted] == [first["id"]]
    assert store.list_outbox(status="bogus-status") == []


def test_mark_outbox_posted_sets_status_and_timestamp(store):
    session = _session(store)
    row = store.enqueue_slack(session["id"], "to be posted")
    _tick()
    posted = store.mark_outbox_posted(row["id"])
    assert posted["status"] == "posted"
    assert posted["posted_at"] is not None
    assert posted["posted_at"] >= posted["created_at"]
    fetched = store.list_outbox(status="posted")
    assert [r["id"] for r in fetched] == [row["id"]]


def test_mark_outbox_posted_missing_returns_none(store):
    assert store.mark_outbox_posted("no-such-id") is None


def test_list_outbox_empty(store):
    assert store.list_outbox() == []
    assert store.list_outbox(status="queued") == []


# ----------------------------------------------------------------------
# FakeVectorStore
# ----------------------------------------------------------------------


def test_fake_store_known_collections_start_empty(vectors):
    for collection in COLLECTIONS:
        assert vectors.count(collection) == 0
        assert vectors.query(collection, [1.0, 0.0]) == []
        assert vectors.get(collection, "anything") is None


def test_fake_upsert_and_count_per_collection(vectors):
    vectors.upsert(COLLECTION_FACE, "face-1", [1.0, 0.0], {"person_id": "p1"})
    vectors.upsert(COLLECTION_FACE, "face-2", [0.0, 1.0], {"person_id": "p2"})
    vectors.upsert(COLLECTION_VOICE, "voice-1", [1.0, 0.0], {"person_id": "p1"})
    assert vectors.count(COLLECTION_FACE) == 2
    assert vectors.count(COLLECTION_VOICE) == 1
    assert vectors.count(COLLECTION_CHUNKS) == 0


def test_fake_upsert_same_id_overwrites_without_growing(vectors):
    vectors.upsert(
        COLLECTION_CHUNKS, "chunk-1", [1.0, 0.0], {"v": 1}, document="old"
    )
    vectors.upsert(
        COLLECTION_CHUNKS, "chunk-1", [0.0, 1.0], {"v": 2}, document="new"
    )
    assert vectors.count(COLLECTION_CHUNKS) == 1
    hit = vectors.get(COLLECTION_CHUNKS, "chunk-1")
    assert hit.metadata == {"v": 2}
    assert hit.document == "new"
    hits = vectors.query(COLLECTION_CHUNKS, [0.0, 1.0], n=1)
    assert hits[0].score == pytest.approx(1.0)


def test_fake_query_orders_by_cosine_similarity(vectors):
    vectors.upsert(COLLECTION_FACE, "exact", [1.0, 0.0], {})
    vectors.upsert(COLLECTION_FACE, "diag", [1.0, 1.0], {})
    vectors.upsert(COLLECTION_FACE, "orth", [0.0, 1.0], {})
    vectors.upsert(COLLECTION_FACE, "opposite", [-1.0, 0.0], {})
    hits = vectors.query(COLLECTION_FACE, [1.0, 0.0], n=4)
    assert [h.id for h in hits] == ["exact", "diag", "orth", "opposite"]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[1].score == pytest.approx(0.70710678, abs=1e-6)
    assert hits[2].score == pytest.approx(0.0)
    assert hits[3].score == pytest.approx(-1.0)


def test_fake_query_respects_n(vectors):
    for idx in range(4):
        vectors.upsert(COLLECTION_FACE, f"id-{idx}", [1.0, 0.0], {})
    assert len(vectors.query(COLLECTION_FACE, [1.0, 0.0], n=2)) == 2
    assert len(vectors.query(COLLECTION_FACE, [1.0, 0.0], n=1)) == 1
    assert len(vectors.query(COLLECTION_FACE, [1.0, 0.0])) == 4
    assert len(vectors.query(COLLECTION_FACE, [1.0, 0.0], n=99)) == 4


def test_fake_query_non_positive_n_returns_empty(vectors):
    vectors.upsert(COLLECTION_FACE, "only", [1.0, 0.0], {})
    assert vectors.query(COLLECTION_FACE, [1.0, 0.0], n=0) == []
    assert vectors.query(COLLECTION_FACE, [1.0, 0.0], n=-3) == []


def test_fake_query_ties_break_by_id(vectors):
    vectors.upsert(COLLECTION_FACE, "b-id", [1.0, 0.0], {})
    vectors.upsert(COLLECTION_FACE, "a-id", [1.0, 0.0], {})
    vectors.upsert(COLLECTION_FACE, "c-id", [1.0, 0.0], {})
    hits = vectors.query(COLLECTION_FACE, [1.0, 0.0], n=3)
    assert [h.id for h in hits] == ["a-id", "b-id", "c-id"]
    assert all(h.score == pytest.approx(1.0) for h in hits)


def test_fake_query_zero_vectors_score_zero(vectors):
    vectors.upsert(COLLECTION_FACE, "zero", [0.0, 0.0], {})
    vectors.upsert(COLLECTION_FACE, "unit", [1.0, 0.0], {})
    hits = vectors.query(COLLECTION_FACE, [1.0, 0.0], n=2)
    by_id = {h.id: h.score for h in hits}
    assert by_id["zero"] == pytest.approx(0.0)
    zero_query = vectors.query(COLLECTION_FACE, [0.0, 0.0], n=2)
    assert all(h.score == pytest.approx(0.0) for h in zero_query)


def test_fake_get_returns_hit_with_score_one(vectors):
    vectors.upsert(
        COLLECTION_VOICE,
        "voice-9",
        [0.5, 0.5],
        {"person_id": "p9", "kind": "voice"},
        document="sample doc",
    )
    hit = vectors.get(COLLECTION_VOICE, "voice-9")
    assert hit is not None
    assert hit.id == "voice-9"
    assert hit.score == 1.0
    assert hit.metadata == {"person_id": "p9", "kind": "voice"}
    assert hit.document == "sample doc"


def test_fake_get_missing_returns_none(vectors):
    vectors.upsert(COLLECTION_VOICE, "present", [1.0], {})
    assert vectors.get(COLLECTION_VOICE, "absent") is None


def test_fake_delete_removes_entry(vectors):
    vectors.upsert(COLLECTION_FACE, "keep", [1.0, 0.0], {})
    vectors.upsert(COLLECTION_FACE, "drop", [0.0, 1.0], {})
    vectors.delete(COLLECTION_FACE, "drop")
    assert vectors.count(COLLECTION_FACE) == 1
    assert vectors.get(COLLECTION_FACE, "drop") is None
    hits = vectors.query(COLLECTION_FACE, [0.0, 1.0], n=5)
    assert [h.id for h in hits] == ["keep"]


def test_fake_delete_missing_id_is_a_noop(vectors):
    vectors.upsert(COLLECTION_FACE, "only", [1.0, 0.0], {})
    vectors.delete(COLLECTION_FACE, "never-existed")
    assert vectors.count(COLLECTION_FACE) == 1
    assert vectors.get(COLLECTION_FACE, "only") is not None


def test_fake_collections_are_isolated(vectors):
    vectors.upsert(COLLECTION_FACE, "shared-id", [1.0, 0.0], {"src": "face"})
    vectors.upsert(
        COLLECTION_VOICE, "shared-id", [0.0, 1.0], {"src": "voice"}
    )
    assert vectors.get(COLLECTION_FACE, "shared-id").metadata == {"src": "face"}
    assert vectors.get(COLLECTION_VOICE, "shared-id").metadata == {
        "src": "voice"
    }
    vectors.delete(COLLECTION_FACE, "shared-id")
    assert vectors.count(COLLECTION_FACE) == 0
    assert vectors.count(COLLECTION_VOICE) == 1
    assert vectors.get(COLLECTION_VOICE, "shared-id") is not None


def test_fake_unknown_collection_behaves_like_an_empty_one(vectors):
    unknown = "no_such_collection"
    assert unknown not in COLLECTIONS
    assert vectors.count(unknown) == 0
    assert vectors.query(unknown, [1.0, 0.0]) == []
    assert vectors.get(unknown, "anything") is None
    vectors.delete(unknown, "anything")
    assert vectors.count(unknown) == 0


def test_fake_unknown_collection_accepts_writes(vectors):
    unknown = "custom_collection"
    vectors.upsert(unknown, "entry-1", [1.0, 0.0], {"k": "v"}, document="d")
    assert vectors.count(unknown) == 1
    hit = vectors.get(unknown, "entry-1")
    assert hit.metadata == {"k": "v"}
    assert hit.document == "d"
    hits = vectors.query(unknown, [1.0, 0.0], n=5)
    assert [h.id for h in hits] == ["entry-1"]
    assert vectors.count(COLLECTION_FACE) == 0


def test_fake_metadata_is_copied_on_write_and_read(vectors):
    metadata = {"person_id": "p1"}
    vectors.upsert(COLLECTION_FACE, "face-1", [1.0, 0.0], metadata)
    metadata["person_id"] = "mutated"
    metadata["extra"] = True
    hit = vectors.get(COLLECTION_FACE, "face-1")
    assert hit.metadata == {"person_id": "p1"}
    hit.metadata["person_id"] = "mutated-again"
    assert vectors.get(COLLECTION_FACE, "face-1").metadata == {
        "person_id": "p1"
    }


def test_fake_query_with_text_embedder_finds_identical_text(
    vectors, text_embedder
):
    vectors.upsert(
        COLLECTION_CHUNKS,
        "chunk-budget",
        text_embedder.embed("quarterly budget review"),
        {"segment_id": "s1"},
        document="quarterly budget review",
    )
    vectors.upsert(
        COLLECTION_CHUNKS,
        "chunk-grocery",
        text_embedder.embed("unrelated grocery list"),
        {"segment_id": "s2"},
        document="unrelated grocery list",
    )
    hits = vectors.query(
        COLLECTION_CHUNKS,
        text_embedder.embed("quarterly budget review"),
        n=2,
    )
    assert hits[0].id == "chunk-budget"
    assert hits[0].score == pytest.approx(1.0)
    assert hits[0].document == "quarterly budget review"
