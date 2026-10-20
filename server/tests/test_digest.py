"""Tests for onrecord.digest and onrecord.slack_poster.

Fixtures are local to this file: every test builds a real Store on a
tmp DATA_DIR (tmp_path) so segments, sessions, and the slack outbox
all live in SQLite under that directory.
"""

from __future__ import annotations

import dataclasses
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from onrecord.config import Settings
from onrecord.db import Store
from onrecord.digest import build_digest, validate_recap
from onrecord.slack_poster import post_digest


VERIFIED_A_TEXT = (
    'Alice confirmed the budget was 42 dollars and said launch ready today. '
    "Budget planning for spring projects moved ahead."
)
VERIFIED_B_TEXT = (
    "Bob noted budget planning needs 7 reviewers for the spring launch review."
)
REVIEW_TEXT = "Carol whispered zebra quixotic secrets in the review segment."
SILENCE_TEXT = ""


def make_store(tmp_path):
    settings = Settings(data_dir=Path(tmp_path))
    store = Store(settings)
    return store, settings


def add_segment(store, session_id, seq, text, gate, speaker, start, end):
    seg = store.add_segment(
        session_id,
        seq,
        text,
        gate_status=gate,
        start_offset_s=start,
        end_offset_s=end,
    )
    store.set_segment_speaker(seg["id"], None, speaker, "MANUAL")
    return store.get_segment(seg["id"])


@pytest.fixture()
def built(tmp_path):
    """Session with 2 VERIFIED, 1 REVIEW, 1 SILENCE segments.

    VERIFIED talk time: Alice 0 to 30 (30s), Bob 30 to 90 (60s).
    REVIEW (Carol, 999s) and SILENCE (Dan) must never feed the judged
    slots: participants, talk time, topics, or action items.
    """
    store, settings = make_store(tmp_path)
    session = store.create_session(title="Budget sync")
    session_id = session["id"]

    verified_a = add_segment(
        store, session_id, 1, VERIFIED_A_TEXT, "VERIFIED", "Alice", 0, 30
    )
    verified_b = add_segment(
        store, session_id, 2, VERIFIED_B_TEXT, "VERIFIED", "Bob", 30, 90
    )
    review = add_segment(
        store, session_id, 3, REVIEW_TEXT, "REVIEW", "Carol", 0, 999
    )
    silence = add_segment(
        store, session_id, 4, SILENCE_TEXT, "SILENCE", "Dan", 90, 95
    )

    return {
        "store": store,
        "settings": settings,
        "session_id": session_id,
        "verified": [verified_a, verified_b],
        "review": review,
        "silence": silence,
        "tmp_path": Path(tmp_path),
    }


def line_starting(text, prefix):
    for line in text.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError("no line starting with " + prefix + " in:\n" + text)


# ----------------------------------------------------------------------
# build_digest: computed slots
# ----------------------------------------------------------------------


def test_counts_line(built):
    digest = build_digest(built["store"], built["session_id"])
    line = line_starting(digest["text"], "Counts:")
    assert line == "Counts: VERIFIED 2, REVIEW 1, SILENCE 1"


def test_participants_only_verified_speakers(built):
    digest = build_digest(built["store"], built["session_id"])
    line = line_starting(digest["text"], "Participants:")
    assert "Alice (1)" in line
    assert "Bob (1)" in line
    assert "Carol" not in line
    assert "Dan" not in line


def test_talk_time_matches_summed_verified_durations(built):
    digest = build_digest(built["store"], built["session_id"])
    line = line_starting(digest["text"], "Talk time:")
    # Alice 30s is 0:30, Bob 60s is 1:00. Carol 999s is REVIEW and excluded.
    assert line == "Talk time: Alice: 0:30, Bob: 1:00"
    assert "Carol" not in line


def test_topics_exclude_stopwords_and_non_verified(built):
    digest = build_digest(built["store"], built["session_id"])
    line = line_starting(digest["text"], "Topics:")
    topics = [t.strip() for t in line[len("Topics:"):].split(",") if t.strip()]
    assert "budget" in topics
    assert "planning" in topics
    assert "spring" in topics
    # Stopwords from the VERIFIED texts must not appear as topics.
    for stopword in ("the", "and", "for", "was"):
        assert stopword not in topics
    # Tokens that only occur in the REVIEW segment must not appear.
    assert "zebra" not in topics
    assert "quixotic" not in topics
    assert "carol" not in topics


def test_action_items_none_flagged_by_default(built):
    digest = build_digest(built["store"], built["session_id"])
    line = line_starting(digest["text"], "Action items")
    assert line == "Action items: none flagged"


def test_action_items_from_verified_action_segment(tmp_path):
    store, _settings = make_store(tmp_path)
    session = store.create_session(title="Action session")
    session_id = session["id"]
    action_text = "ACTION: Bob to send the budget file by Friday"
    seg = add_segment(
        store, session_id, 1, action_text, "VERIFIED", "Bob", 0, 10
    )
    # A REVIEW segment starting with ACTION: must not be flagged.
    add_segment(
        store,
        session_id,
        2,
        "ACTION: Carol review only should not count",
        "REVIEW",
        "Carol",
        10,
        20,
    )

    digest = build_digest(store, session_id)
    assert "Action items: none flagged" not in digest["text"]
    assert "Action items:" in digest["text"]
    assert action_text in digest["text"]
    assert "[seg:" + seg["id"] + "]" in digest["text"]
    assert "Carol review only" not in digest["text"]


# ----------------------------------------------------------------------
# validate_recap
# ----------------------------------------------------------------------


def valid_recap(built):
    seg_a = built["verified"][0]
    return (
        "Alice confirmed the budget was 42 dollars. [seg:"
        + seg_a["id"]
        + '] The phrase "launch ready" was confirmed for 42 dollars. [seg:'
        + seg_a["id"]
        + "]"
    )


def test_validate_recap_valid_has_no_errors(built):
    errors = validate_recap(valid_recap(built), built["verified"])
    assert errors == []


def test_validate_recap_missing_citation_is_error(built):
    recap = "Alice confirmed the budget was 42 dollars."
    errors = validate_recap(recap, built["verified"])
    assert errors
    assert any("citation" in e.lower() for e in errors)


def test_validate_recap_unknown_id_is_error(built):
    recap = "Alice confirmed the budget was 42 dollars. [seg:no-such-segment-id]"
    errors = validate_recap(recap, built["verified"])
    assert errors
    assert any("no-such-segment-id" in e for e in errors)


def test_validate_recap_invented_number_is_error(built):
    seg_a = built["verified"][0]
    recap = (
        "Alice confirmed the budget was 99 dollars. [seg:" + seg_a["id"] + "]"
    )
    errors = validate_recap(recap, built["verified"])
    assert errors
    assert any("99" in e for e in errors)


def test_validate_recap_unverbatim_quote_is_error(built):
    seg_a = built["verified"][0]
    recap = (
        'Alice said "totally invented phrase" today. [seg:'
        + seg_a["id"]
        + "]"
    )
    errors = validate_recap(recap, built["verified"])
    assert errors
    assert any("totally invented phrase" in e for e in errors)


# ----------------------------------------------------------------------
# build_digest: recap gating
# ----------------------------------------------------------------------


def test_build_digest_invalid_recap_is_draft_and_withheld(built):
    bad_recap = "Zebraquixotic recap claim that must be withheld entirely."
    digest = build_digest(
        built["store"], built["session_id"], recap_text=bad_recap
    )
    assert digest["status"] == "DRAFT"
    assert digest["errors"]
    assert bad_recap not in digest["text"]


def test_build_digest_valid_recap_is_postable_and_present(built):
    recap = valid_recap(built)
    digest = build_digest(
        built["store"], built["session_id"], recap_text=recap
    )
    assert digest["status"] == "POSTABLE"
    assert digest["errors"] == []
    assert recap in digest["text"]


# ----------------------------------------------------------------------
# post_digest
# ----------------------------------------------------------------------


def test_post_digest_unconfirmed_queues_only(built):
    store = built["store"]
    result = post_digest(
        store,
        built["session_id"],
        "digest body unconfirmed",
        built["settings"],
        confirmed=False,
    )
    assert result["posted"] is False
    assert result["reason"] == "awaiting_confirmation"
    outbox = store.list_outbox()
    assert len(outbox) == 1
    assert outbox[0]["status"] == "queued"
    assert outbox[0]["id"] == result["outbox_id"]
    assert outbox[0]["body"] == "digest body unconfirmed"


def test_post_digest_confirmed_no_webhook_writes_file(built):
    store = built["store"]
    settings = built["settings"]
    assert not settings.slack_webhook_url
    result = post_digest(
        store,
        built["session_id"],
        "digest body file sink",
        settings,
        confirmed=True,
    )
    assert result["posted"] is True
    outbox = store.list_outbox()
    assert len(outbox) == 1
    assert outbox[0]["status"] == "posted"
    assert outbox[0]["id"] == result["outbox_id"]

    expected = (
        built["tmp_path"]
        / "outbox"
        / (built["session_id"] + "-" + str(result["outbox_id"]) + ".txt")
    )
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == "digest body file sink"
    # Also confirm at least one file landed under data_dir / outbox.
    files = list((built["tmp_path"] / "outbox").glob("*.txt"))
    assert files


def test_post_digest_confirmed_webhook_failure_stays_queued(built, monkeypatch):
    store = built["store"]
    settings = dataclasses.replace(
        built["settings"], slack_webhook_url="https://example.invalid/hook"
    )

    def boom(*args, **kwargs):
        raise urllib.error.URLError("webhook down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)

    # No exception may propagate out of post_digest.
    result = post_digest(
        store,
        built["session_id"],
        "digest body webhook fails",
        settings,
        confirmed=True,
    )
    assert result["posted"] is False
    assert "webhook" in result["reason"]
    outbox = store.list_outbox()
    assert len(outbox) == 1
    assert outbox[0]["status"] == "queued"
    assert outbox[0]["id"] == result["outbox_id"]
