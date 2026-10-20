"""Tests for onrecord.corrections.CorrectionService.

Fixtures are built locally in this file: a real Store on a tmp
DATA_DIR, plus people, a session, and a segment created through the
store methods. No conftest.py is assumed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onrecord.config import Settings
from onrecord.corrections import CorrectionService
from onrecord.db import Store
from onrecord.interfaces import ATTR_MANUAL


def _make_store(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_url=None,
        chroma_dir=tmp_path / "chroma",
    )
    store = Store(settings)
    return store


@pytest.fixture()
def world(tmp_path):
    store = _make_store(tmp_path)
    try:
        alice = store.create_person("Alice Nguyen")
        bob = store.create_person("Bob Patel")
        session = store.create_session(title="corrections session")
        segment = store.add_segment(
            session_id=session["id"],
            seq=1,
            text="hello from the segment",
            raw_text="hello from the segment",
            avg_logprob=-0.2,
            no_speech_prob=0.05,
            gate_status="VERIFIED",
            uncertain=False,
            start_offset_s=0.0,
            end_offset_s=1.0,
        )
        service = CorrectionService(store)
        yield {
            "store": store,
            "service": service,
            "alice": alice,
            "bob": bob,
            "session": session,
            "segment": segment,
        }
    finally:
        store.close()


def test_assign_speaker_sets_segment_and_appends_one_correction(world):
    store = world["store"]
    service = world["service"]
    segment = world["segment"]
    alice = world["alice"]

    assert segment["speaker_person_id"] is None

    correction = service.assign_speaker(segment["id"], alice["id"])

    updated = store.get_segment(segment["id"])
    assert updated is not None
    assert updated["speaker_person_id"] == alice["id"]
    assert updated["speaker_label"] == alice["name"]
    assert updated["attribution_source"] == ATTR_MANUAL

    history = service.history(segment["id"])
    assert len(history) == 1
    row = history[0]
    assert row["id"] == correction["id"]
    assert row["segment_id"] == segment["id"]
    assert row["old_speaker_person_id"] is None
    assert row["new_speaker_person_id"] == alice["id"]


def test_second_assign_appends_second_row_and_first_row_unchanged(world):
    store = world["store"]
    service = world["service"]
    segment = world["segment"]
    alice = world["alice"]
    bob = world["bob"]

    first = service.assign_speaker(segment["id"], alice["id"])
    first_snapshot = dict(first)

    second = service.assign_speaker(segment["id"], bob["id"])

    history = service.history(segment["id"])
    assert len(history) == 2

    # Append-only: the first row is still there, unchanged.
    assert history[0] == first_snapshot
    assert history[0]["id"] == first["id"]
    assert history[0]["old_speaker_person_id"] is None
    assert history[0]["new_speaker_person_id"] == alice["id"]

    assert history[1]["id"] == second["id"]
    assert history[1]["segment_id"] == segment["id"]
    assert history[1]["old_speaker_person_id"] == alice["id"]
    assert history[1]["new_speaker_person_id"] == bob["id"]

    updated = store.get_segment(segment["id"])
    assert updated["speaker_person_id"] == bob["id"]
    assert updated["speaker_label"] == bob["name"]
    assert updated["attribution_source"] == ATTR_MANUAL


def test_unassign_speaker_clears_speaker_and_appends_row(world):
    store = world["store"]
    service = world["service"]
    segment = world["segment"]
    alice = world["alice"]

    service.assign_speaker(segment["id"], alice["id"])
    correction = service.unassign_speaker(segment["id"])

    updated = store.get_segment(segment["id"])
    assert updated is not None
    assert updated["speaker_person_id"] is None
    assert updated["speaker_label"] == "Unknown"
    assert updated["attribution_source"] == ATTR_MANUAL

    assert correction["segment_id"] == segment["id"]
    assert correction["old_speaker_person_id"] == alice["id"]
    assert correction["new_speaker_person_id"] is None

    history = service.history(segment["id"])
    assert len(history) == 2
    assert history[1]["id"] == correction["id"]
    assert history[1]["old_speaker_person_id"] == alice["id"]
    assert history[1]["new_speaker_person_id"] is None


def test_assign_with_unknown_segment_raises_keyerror(world):
    service = world["service"]
    alice = world["alice"]

    with pytest.raises(KeyError):
        service.assign_speaker("no-such-segment-id", alice["id"])


def test_assign_with_unknown_person_raises_keyerror(world):
    service = world["service"]
    segment = world["segment"]

    with pytest.raises(KeyError):
        service.assign_speaker(segment["id"], "no-such-person-id")

    # A failed assign must not leave a correction row or a speaker behind.
    assert service.history(segment["id"]) == []
    updated = world["store"].get_segment(segment["id"])
    assert updated["speaker_person_id"] is None


def test_unassign_with_unknown_segment_raises_keyerror(world):
    service = world["service"]

    with pytest.raises(KeyError):
        service.unassign_speaker("no-such-segment-id")
