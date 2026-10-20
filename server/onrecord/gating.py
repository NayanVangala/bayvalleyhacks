"""Confidence gate for transcription (BUILD_PLAN Section 2.5).

Rule: nothing reaches a judged artifact unless it passed this gate;
low confidence is flagged, never silently dropped or silently trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .interfaces import GATE_REVIEW, GATE_SILENCE, GATE_VERIFIED, RawSegment

SILENCE_NO_SPEECH = 0.6
VERIFIED_LOGPROB = -0.5
LOW_CONF_LOGPROB = -1.0


@dataclass
class GateResult:
    status: str
    uncertain: bool
    low_conf: bool
    display_text: str


def gate_segment(
    avg_logprob: float, no_speech_prob: float, text: Optional[str]
) -> GateResult:
    if no_speech_prob >= SILENCE_NO_SPEECH or text is None or text.strip() == "":
        return GateResult(
            status=GATE_SILENCE,
            uncertain=False,
            low_conf=False,
            display_text="",
        )
    if avg_logprob >= VERIFIED_LOGPROB:
        return GateResult(
            status=GATE_VERIFIED,
            uncertain=False,
            low_conf=False,
            display_text=text,
        )
    return GateResult(
        status=GATE_REVIEW,
        uncertain=True,
        low_conf=avg_logprob < LOW_CONF_LOGPROB,
        display_text="[uncertain]",
    )


def gate_raw(raw: RawSegment) -> GateResult:
    return gate_segment(raw.avg_logprob, raw.no_speech_prob, raw.text)


def is_verified(status: str) -> bool:
    return status == GATE_VERIFIED
