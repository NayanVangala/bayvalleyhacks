"""Demo contract tests that the per-module suites do not pin down.

Covered here:
  Identity fusion at the exact threshold boundaries, using prescribed
  vector hits (conftest.StubVectorStore) instead of cosine geometry.
  Digest checker rejection of invented proper nouns and dates, of
  numbers borrowed from a segment the sentence does not cite, and of
  citations pointing at non-VERIFIED segments.
  Retrieval: an ambiguous name returns its candidates with zero
  segments, and exact search carries the same reason codes as
  semantic search.
  State machine transitions the happy path suites do not try, and
  the empty-pause pre-roll rule.
"""

from __future__ import annotations

import pytest

from conftest import StubVectorStore, hit
from onrecord.digest import build_digest, validate_recap
from onrecord.identity import EnrollmentService, FusionMatcher
from onrecord.interfaces import (
    ATTR_FACE_ONLY,
    ATTR_FUSION,
    ATTR_UNKNOWN,
    ATTR_VOICE_ONLY,
    COLLECTION_CHUNKS,
    COLLECTION_FACE,
    COLLECTION_VOICE,
)
from onrecord.query import QueryService
from onrecord.state_machine import (
    InvalidTransition,
    SessionController,
    SessionState,
)

# ----------------------------------------------------------------------
# Identity fusion: exact thresholds
# ----------------------------------------------------------------------

P1 = "person-1"


def matcher_with(face_hits=None, voice_hits=None, settings=None):
    vectors = StubVectorStore(
        {
            COLLECTION_FACE: list(face_hits or []),
            COLLECTION_VOICE: list(voice_hits or []),
        }
    )
    return FusionMatcher(vectors, settings)


def test_fusion_at_exact_thresholds_names_the_speaker(settings):
    # Settings defaults: face 0.45, voice 0.75. Exactly at is a pass.
    matcher = matcher_with(
        [hit(P1, 0.45, "face")], [hit(P1, 0.75, "voice")], settings
    )
    result = matcher.identify(face_embedding=[1.0], voice_embedding=[1.0])
    assert result.person_id == P1
    assert result.source == ATTR_FUSION


@pytest.mark.parametrize(
    "face_score,voice_score",
    [(0.4499, 0.99), (0.99, 0.7499), (0.4499, 0.7499), (0.0, 0.0)],
)
def test_fusion_just_below_either_threshold_is_unknown(settings, face_score, voice_score):
    matcher = matcher_with(
        [hit(P1, face_score, "face")], [hit(P1, voice_score, "voice")], settings
    )
    result = matcher.identify(face_embedding=[1.0], voice_embedding=[1.0])
    assert result.person_id is None
    assert result.source == ATTR_UNKNOWN
    # The near miss is still recorded for inspection, not hidden.
    assert result.face_person_id == P1
    assert result.voice_person_id == P1


def test_fusion_missing_voice_hit_is_unknown(settings):
    matcher = matcher_with([hit(P1, 0.99, "face")], [], settings)
    result = matcher.identify(face_embedding=[1.0], voice_embedding=[1.0])
    assert result.person_id is None
    assert result.source == ATTR_UNKNOWN
    assert result.face_person_id == P1
    assert result.voice_person_id is None


def test_fusion_missing_face_hit_is_unknown(settings):
    matcher = matcher_with([], [hit(P1, 0.99, "voice")], settings)
    result = matcher.identify(face_embedding=[1.0], voice_embedding=[1.0])
    assert result.person_id is None
    assert result.source == ATTR_UNKNOWN
    assert result.face_person_id is None
    assert result.voice_person_id == P1


def test_face_only_at_exact_threshold_names_the_speaker(settings):
    matcher = matcher_with([hit(P1, 0.60, "face")], [], settings)
    result = matcher.identify(face_embedding=[1.0])
    assert result.person_id == P1
    assert result.source == ATTR_FACE_ONLY


def test_face_only_just_below_threshold_is_unknown(settings):
    # 0.59 clears the fusion face threshold (0.45) but single modality
    # uses its own stricter threshold (0.60), so it must not name.
    matcher = matcher_with([hit(P1, 0.59, "face")], [], settings)
    result = matcher.identify(face_embedding=[1.0])
    assert result.person_id is None
    assert result.source == ATTR_UNKNOWN


def test_voice_only_at_exact_threshold_names_the_speaker(settings):
    matcher = matcher_with([], [hit(P1, 0.85, "voice")], settings)
    result = matcher.identify(voice_embedding=[1.0])
    assert result.person_id == P1
    assert result.source == ATTR_VOICE_ONLY


def test_voice_only_just_below_threshold_is_unknown(settings):
    matcher = matcher_with([], [hit(P1, 0.8499, "voice")], settings)
    result = matcher.identify(voice_embedding=[1.0])
    assert result.person_id is None
    assert result.source == ATTR_UNKNOWN


class _FaceOkVoiceNone:
    # Enrollment validates dimensions: face prints are 512 and voice
    # prints are 192 (interfaces DIM_FACE and DIM_VOICE), so the
    # doubles return unit vectors of exactly those lengths.
    def embed_image(self, image_bytes):
        return [1.0] + [0.0] * 511

    def embed_pcm(self, pcm, sample_rate=16000):
        return None


class _FaceOkVoiceOk(_FaceOkVoiceNone):
    def embed_pcm(self, pcm, sample_rate=16000):
        return [1.0] + [0.0] * 191


def test_enroll_voice_failure_creates_no_person(store, vectors):
    service = EnrollmentService(store, vectors, _FaceOkVoiceNone(), _FaceOkVoiceNone())
    with pytest.raises(ValueError):
        service.enroll_person("Maya Chen", b"photo", b"\x00" * 16000)
    assert store.people_count() == 0
    assert vectors.count(COLLECTION_FACE) == 0
    assert vectors.count(COLLECTION_VOICE) == 0


def test_enroll_success_stores_both_prints(store, vectors):
    service = EnrollmentService(store, vectors, _FaceOkVoiceOk(), _FaceOkVoiceOk())
    person = service.enroll_person("Maya Chen", b"photo", b"\x00" * 16000)
    assert store.people_count() == 1
    assert vectors.count(COLLECTION_FACE) == 1
    assert vectors.count(COLLECTION_VOICE) == 1
    face_hit = vectors.get(COLLECTION_FACE, "face:" + person["id"])
    assert face_hit.metadata["person_id"] == person["id"]


# ----------------------------------------------------------------------
# Digest checker: invented content beyond numbers and quotes
# ----------------------------------------------------------------------

SEG_BUDGET = {"id": "s1", "text": "The budget is 500 dollars for catering."}
SEG_VENUE = {"id": "s2", "text": "The venue holds 300 people in the main hall."}
SEG_MAYA = {"id": "s3", "text": "Maya set the budget at 500 dollars."}


def test_recap_invented_proper_noun_mid_sentence_is_rejected():
    recap = "The budget of 500 dollars was approved by Maya. [seg:s1]"
    errors = validate_recap(recap, [SEG_BUDGET])
    assert errors
    assert any("Maya" in e for e in errors)


def test_recap_invented_proper_noun_at_sentence_start_is_rejected():
    recap = "Maya approved the budget of 500 dollars. [seg:s1]"
    errors = validate_recap(recap, [SEG_BUDGET])
    assert errors
    assert any("Maya" in e for e in errors)


def test_recap_invented_month_is_rejected():
    recap = "The budget of 500 dollars is due in March. [seg:s1]"
    errors = validate_recap(recap, [SEG_BUDGET])
    assert errors
    assert any("March" in e for e in errors)


def test_recap_proper_noun_present_in_cited_segment_passes():
    recap = "Maya set the budget at 500 dollars. [seg:s3]"
    assert validate_recap(recap, [SEG_MAYA]) == []


def test_recap_number_from_uncited_segment_is_rejected():
    # 300 exists in a verified segment, just not the one cited. The
    # checker rule is containment in the cited segments, not anywhere.
    recap = "The budget is 300 dollars for catering. [seg:s1]"
    errors = validate_recap(recap, [SEG_BUDGET, SEG_VENUE])
    assert errors
    assert any("300" in e for e in errors)


def test_recap_citing_a_review_segment_is_rejected():
    # validate_recap only receives VERIFIED segments, so a citation to
    # a REVIEW segment id must fail as not verified.
    recap = "The budget is 500 dollars for catering. [seg:review-9]"
    errors = validate_recap(recap, [SEG_BUDGET])
    assert errors
    assert any("review-9" in e for e in errors)


def test_build_digest_with_invented_name_recap_is_draft(store):
    session = store.create_session(title="Budget sync")
    seg = store.add_segment(
        session["id"], 0, SEG_BUDGET["text"], gate_status="VERIFIED",
        start_offset_s=0, end_offset_s=5,
    )
    recap = "Zelda approved the budget of 500 dollars. [seg:%s]" % seg["id"]
    digest = build_digest(store, session["id"], recap_text=recap)
    assert digest["status"] == "DRAFT"
    assert digest["errors"]
    assert "Zelda" not in digest["text"]
    assert "[UNVERIFIED" in digest["text"]


# ----------------------------------------------------------------------
# Retrieval: ambiguous candidates and exact search reason codes
# ----------------------------------------------------------------------


@pytest.fixture()
def query_env(store, vectors, text_embedder):
    return QueryService(store, vectors, text_embedder), store, vectors, text_embedder


def _person_with_budget_segment(store, vectors, text_embedder, name):
    person = store.create_person(name)
    session = store.create_session(title="sync")
    seg = store.add_segment(
        session["id"], 0, "The budget is 500 dollars for catering.",
        gate_status="VERIFIED", start_offset_s=0, end_offset_s=5,
    )
    store.set_segment_speaker(
        seg["id"], person["id"], name, "FUSION_FACE_VOICE",
        face_score=0.9, voice_score=0.9,
    )
    seg = store.get_segment(seg["id"])
    vectors.upsert(
        COLLECTION_CHUNKS, seg["id"], text_embedder.embed(seg["text"]),
        {"segment_id": seg["id"], "session_id": session["id"]},
        document=seg["text"],
    )
    return person


def test_ambiguous_name_returns_candidates_and_zero_segments(query_env):
    service, store, vectors, text_embedder = query_env
    first = _person_with_budget_segment(store, vectors, text_embedder, "Alex Rivera")
    second = _person_with_budget_segment(store, vectors, text_embedder, "Alex Rivera")
    assert first["id"] != second["id"]

    for result in (
        service.search_conversations("budget", person_ref="Alex Rivera"),
        service.exact_search("budget", person_ref="Alex Rivera"),
    ):
        assert result.status == "EMPTY"
        assert result.reason == "AMBIGUOUS_NAME"
        assert result.results == []
        candidate_ids = {c["id"] for c in result.candidates}
        assert candidate_ids == {first["id"], second["id"]}
        assert all(c["name"] == "Alex Rivera" for c in result.candidates)


def test_exact_search_no_match_carries_no_matches_reason(query_env):
    service, store, vectors, text_embedder = query_env
    _person_with_budget_segment(store, vectors, text_embedder, "Maya Chen")
    result = service.exact_search("nonexistentterm")
    assert result.status == "EMPTY"
    assert result.reason == "NO_MATCHES"
    assert result.results == []


def test_exact_search_person_with_only_review_carries_no_verified(query_env):
    service, store, vectors, text_embedder = query_env
    rita = store.create_person("Rita Review")
    session = store.create_session(title="sync")
    seg = store.add_segment(
        session["id"], 0, "Rita discussed the budget in review.",
        gate_status="REVIEW", start_offset_s=0, end_offset_s=5,
    )
    store.set_segment_speaker(
        seg["id"], rita["id"], "Rita Review", "FUSION_FACE_VOICE",
        face_score=0.9, voice_score=0.9,
    )
    result = service.exact_search("budget", person_ref="Rita Review")
    assert result.status == "EMPTY"
    assert result.reason == "NO_VERIFIED_SEGMENTS"
    assert result.results == []


def test_exact_search_ambiguous_and_unknown_are_distinct(query_env):
    service, store, vectors, text_embedder = query_env
    _person_with_budget_segment(store, vectors, text_embedder, "Maya Chen")
    unknown = service.exact_search("budget", person_ref="Nobody Here")
    assert unknown.status == "EMPTY"
    assert unknown.reason == "PERSON_NOT_FOUND"
    assert unknown.candidates == []


# ----------------------------------------------------------------------
# State machine: transitions and pre-roll rules not covered elsewhere
# ----------------------------------------------------------------------


def _controller():
    # 8000 Hz, 1 second of pre-roll: capacities stay tiny and fast.
    return SessionController(sample_rate=8000, preroll_s=1)


def _pcm(fill, samples=800):
    return bytes([fill]) * (samples * 2)


@pytest.mark.parametrize(
    "setup,action",
    [
        ("ended", "end"),
        ("ended", "arm"),
        ("recording", "arm"),
        ("recording", "start"),
        ("recording", "resume"),
        ("paused", "pause"),
        ("paused", "new_segment"),
        ("armed", "resume"),
        ("armed", "pause"),
    ],
)
def test_illegal_transitions_raise_and_keep_state(setup, action):
    controller = _controller()
    if setup in ("recording", "paused", "ended"):
        controller.arm()
        controller.start()
    if setup == "paused":
        controller.pause()
    if setup == "ended":
        controller.end()
    if setup == "armed":
        controller.arm()
    before = controller.state
    with pytest.raises(InvalidTransition):
        getattr(controller, action)()
    assert controller.state == before


def test_empty_pause_does_not_consume_preroll():
    controller = _controller()
    controller.push_audio(_pcm(7))  # fills half the 1s ring
    controller.arm()
    controller.start()
    assert controller.pause() is None  # nothing recorded yet
    controller.resume()
    controller.push_audio(_pcm(9))
    seg = controller.new_segment()
    assert seg.seq == 0
    assert seg.preroll is True
    # Pre-roll bytes come first, then the post-resume audio.
    assert seg.pcm[: len(_pcm(7))] == _pcm(7)
    assert seg.pcm[len(_pcm(7)):] == _pcm(9)
    assert controller.state == SessionState.RECORDING
