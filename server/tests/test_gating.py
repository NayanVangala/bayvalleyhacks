"""Tests for the confidence gate (BUILD_PLAN Section 2.5).

Covers gate_segment, gate_raw, and is_verified in onrecord.gating:
VERIFIED, REVIEW, and SILENCE outcomes, including every boundary value.
"""

from __future__ import annotations

import pytest

from onrecord.gating import GateResult, gate_raw, gate_segment, is_verified
from onrecord.interfaces import (
    GATE_REVIEW,
    GATE_SILENCE,
    GATE_VERIFIED,
    RawSegment,
)

REAL_TEXT = "the meeting starts at nine"


# VERIFIED


def test_verified_happy_path():
    result = gate_segment(avg_logprob=-0.2, no_speech_prob=0.05, text=REAL_TEXT)
    assert result.status == GATE_VERIFIED
    assert result.uncertain is False
    assert result.low_conf is False
    assert result.display_text == REAL_TEXT


def test_verified_boundary_avg_logprob_exactly_minus_0_5():
    result = gate_segment(avg_logprob=-0.5, no_speech_prob=0.05, text=REAL_TEXT)
    assert result.status == GATE_VERIFIED
    assert result.uncertain is False
    assert result.low_conf is False
    assert result.display_text == REAL_TEXT


# REVIEW


def test_review_just_below_verified_boundary():
    result = gate_segment(avg_logprob=-0.51, no_speech_prob=0.05, text=REAL_TEXT)
    assert result.status == GATE_REVIEW
    assert result.uncertain is True
    assert result.display_text == "[uncertain]"


def test_review_display_text_and_flags_at_minus_0_7():
    result = gate_segment(avg_logprob=-0.7, no_speech_prob=0.05, text=REAL_TEXT)
    assert result.status == GATE_REVIEW
    assert result.display_text == "[uncertain]"
    assert result.uncertain is True
    assert result.low_conf is False


def test_review_low_conf_true_at_minus_1_5():
    result = gate_segment(avg_logprob=-1.5, no_speech_prob=0.05, text=REAL_TEXT)
    assert result.status == GATE_REVIEW
    assert result.display_text == "[uncertain]"
    assert result.uncertain is True
    assert result.low_conf is True


def test_review_low_conf_boundary_exactly_minus_1_0():
    # Rule is avg_logprob < -1.0, so exactly -1.0 is not low_conf.
    result = gate_segment(avg_logprob=-1.0, no_speech_prob=0.05, text=REAL_TEXT)
    assert result.status == GATE_REVIEW
    assert result.uncertain is True
    assert result.low_conf is False
    assert result.display_text == "[uncertain]"


def test_review_low_conf_just_below_minus_1_0():
    result = gate_segment(avg_logprob=-1.01, no_speech_prob=0.05, text=REAL_TEXT)
    assert result.status == GATE_REVIEW
    assert result.low_conf is True


# SILENCE


def test_silence_no_speech_prob_exactly_0_6():
    result = gate_segment(avg_logprob=-0.2, no_speech_prob=0.6, text=REAL_TEXT)
    assert result.status == GATE_SILENCE
    assert result.display_text == ""
    assert result.uncertain is False
    assert result.low_conf is False


def test_no_speech_prob_0_59_with_good_logprob_is_verified():
    result = gate_segment(avg_logprob=-0.2, no_speech_prob=0.59, text=REAL_TEXT)
    assert result.status == GATE_VERIFIED
    assert result.uncertain is False
    assert result.display_text == REAL_TEXT


def test_silence_no_speech_prob_above_threshold():
    result = gate_segment(avg_logprob=-0.2, no_speech_prob=0.9, text=REAL_TEXT)
    assert result.status == GATE_SILENCE
    assert result.display_text == ""


@pytest.mark.parametrize("avg_logprob", [-0.2, -0.5, -0.7, -1.5, 0.0])
def test_silence_empty_text_regardless_of_logprob(avg_logprob):
    result = gate_segment(avg_logprob=avg_logprob, no_speech_prob=0.05, text="")
    assert result.status == GATE_SILENCE
    assert result.display_text == ""
    assert result.uncertain is False
    assert result.low_conf is False


@pytest.mark.parametrize("text", [" ", "   ", "\t", "\n", "  \t\n  "])
def test_silence_whitespace_only_text(text):
    result = gate_segment(avg_logprob=-0.2, no_speech_prob=0.05, text=text)
    assert result.status == GATE_SILENCE
    assert result.display_text == ""
    assert result.uncertain is False
    assert result.low_conf is False


@pytest.mark.parametrize("avg_logprob", [-0.2, -0.5, -1.5, 0.0])
def test_silence_none_text_regardless_of_logprob(avg_logprob):
    result = gate_segment(avg_logprob=avg_logprob, no_speech_prob=0.05, text=None)
    assert result.status == GATE_SILENCE
    assert result.display_text == ""
    assert result.uncertain is False
    assert result.low_conf is False


# gate_raw


def _raw(avg_logprob, no_speech_prob, text):
    return RawSegment(
        text=text,
        avg_logprob=avg_logprob,
        no_speech_prob=no_speech_prob,
        start=0.0,
        end=1.0,
    )


@pytest.mark.parametrize(
    "avg_logprob,no_speech_prob,text",
    [
        (-0.2, 0.05, REAL_TEXT),
        (-0.5, 0.05, REAL_TEXT),
        (-0.51, 0.05, REAL_TEXT),
        (-0.7, 0.05, REAL_TEXT),
        (-1.0, 0.05, REAL_TEXT),
        (-1.5, 0.05, REAL_TEXT),
        (-0.2, 0.6, REAL_TEXT),
        (-0.2, 0.59, REAL_TEXT),
        (-0.2, 0.05, ""),
        (-0.2, 0.05, "   "),
    ],
)
def test_gate_raw_matches_gate_segment(avg_logprob, no_speech_prob, text):
    raw = _raw(avg_logprob, no_speech_prob, text)
    expected = gate_segment(avg_logprob, no_speech_prob, text)
    result = gate_raw(raw)
    assert isinstance(result, GateResult)
    assert result == expected
    assert result.status == expected.status
    assert result.uncertain == expected.uncertain
    assert result.low_conf == expected.low_conf
    assert result.display_text == expected.display_text


def test_gate_raw_verified_result():
    result = gate_raw(_raw(-0.2, 0.05, REAL_TEXT))
    assert result.status == GATE_VERIFIED
    assert result.uncertain is False
    assert result.display_text == REAL_TEXT


# is_verified


def test_is_verified_true_only_for_verified():
    assert is_verified(GATE_VERIFIED) is True
    assert is_verified(GATE_REVIEW) is False
    assert is_verified(GATE_SILENCE) is False


@pytest.mark.parametrize("status", ["", "verified", "UNKNOWN", "OK", None])
def test_is_verified_false_for_other_values(status):
    assert is_verified(status) is False


def test_is_verified_matches_gate_outcomes():
    verified = gate_segment(avg_logprob=-0.2, no_speech_prob=0.05, text=REAL_TEXT)
    review = gate_segment(avg_logprob=-0.7, no_speech_prob=0.05, text=REAL_TEXT)
    silence = gate_segment(avg_logprob=-0.2, no_speech_prob=0.6, text=REAL_TEXT)
    assert is_verified(verified.status) is True
    assert is_verified(review.status) is False
    assert is_verified(silence.status) is False
