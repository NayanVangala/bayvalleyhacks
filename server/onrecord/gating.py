"""Confidence gate for transcription (BUILD_PLAN Section 2.5).

Rule: nothing reaches a judged artifact unless it passed this gate;
low confidence is flagged, never silently dropped or silently trusted.

Buckets (Revision 2):
- SILENCE if no_speech_prob >= 0.6 or the text is empty. Stored, excluded
  from every judged artifact.
- VERIFIED if avg_logprob >= -0.5. The only bucket allowed into judged
  answers and digests.
- REVIEW otherwise. The band -1.0 <= avg_logprob < -0.5 is plain REVIEW;
  below -1.0 is REVIEW with low_conf set (Whisper decode failure range).
  Flagged, stored, excluded from answers.

The gate fails closed: a segment whose confidence metrics are missing
or not finite numbers (None, NaN, inf, non-numeric) can never be
VERIFIED. With real text present it lands in REVIEW with low_conf set
instead of crashing the pipeline or slipping through as trusted.
"""

from __future__ import annotations

import math
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


def _finite_number(value) -> Optional[float]:
    """Return value as a finite float, or None if it is not usable.

    None, NaN, infinities, and non-numeric values all count as missing:
    a confidence metric that cannot be compared cannot prove anything.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def gate_segment(
    avg_logprob: Optional[float],
    no_speech_prob: Optional[float],
    text: Optional[str],
) -> GateResult:
    # Empty text is silence no matter what the metrics claim, and this
    # check runs first so missing metrics on an empty segment can never
    # crash or mis-gate it.
    if text is None or str(text).strip() == "":
        return GateResult(
            status=GATE_SILENCE,
            uncertain=False,
            low_conf=False,
            display_text="",
        )

    logprob = _finite_number(avg_logprob)
    no_speech = _finite_number(no_speech_prob)

    # Provable silence outranks everything else, including a good
    # logprob and including a missing logprob.
    if no_speech is not None and no_speech >= SILENCE_NO_SPEECH:
        return GateResult(
            status=GATE_SILENCE,
            uncertain=False,
            low_conf=False,
            display_text="",
        )

    # Fail closed: without both metrics as finite numbers the segment
    # cannot be trusted, but it does have text, so it is not provably
    # silence either. REVIEW with low_conf is the honest bucket.
    if logprob is None or no_speech is None:
        return GateResult(
            status=GATE_REVIEW,
            uncertain=True,
            low_conf=True,
            display_text="[uncertain]",
        )

    if logprob >= VERIFIED_LOGPROB:
        return GateResult(
            status=GATE_VERIFIED,
            uncertain=False,
            low_conf=False,
            display_text=text,
        )
    return GateResult(
        status=GATE_REVIEW,
        uncertain=True,
        low_conf=logprob < LOW_CONF_LOGPROB,
        display_text="[uncertain]",
    )


def gate_raw(raw: RawSegment) -> GateResult:
    return gate_segment(raw.avg_logprob, raw.no_speech_prob, raw.text)


def is_verified(status: str) -> bool:
    return status == GATE_VERIFIED
