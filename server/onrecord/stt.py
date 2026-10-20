"""Speech-to-text for OnRecord, behind the Transcriber protocol.

The real implementation wraps faster-whisper. That library is heavy, so it
is lazy-imported inside the FasterWhisperTranscriber constructor only; this
module imports cleanly on machines without it.

Pinned decode contract (Revision 2, BUILD_PLAN Section 1.5): model is an
English-only faster-whisper model (default small.en), compute_type int8,
language locked to "en" and verified against the transcription info on
every call, temperature 0.0, beam_size 5, VAD off, no conditioning on
previous text. Same audio in, byte-identical transcript out.

FakeTranscriber is a deterministic test double, clearly labeled as a fake.
It exists for tests and offline smoke runs. It is not a real transcriber
and must never be presented as one. get_transcriber returns it ONLY when
ONRECORD_STT=fake is set explicitly; a real model that fails to load is a
hard error, never a silent fallback, because the fake's canned segment
gates as VERIFIED and would otherwise fabricate judged transcripts.

Design note: VAD stays OFF by design. The session state machine already
decides what counts as a sealed segment, so whisper-side voice activity
detection would only double-gate audio and risk dropping speech.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import wave
from typing import Any, Optional, Sequence

from .interfaces import RawSegment

# Audio shorter than this is not worth a model call.
MIN_AUDIO_S = 0.3

FAKE_TEXT = "this is a fake transcript segment"

# Pinned decode contract. Do not loosen these without updating the plan:
# they are what make a re-run of the same audio byte-identical.
LANGUAGE = "en"
MIN_LANGUAGE_PROBABILITY = 0.9
DECODE_TEMPERATURE = 0.0
DECODE_BEAM_SIZE = 5
COMPUTE_TYPE = "int8"
DEFAULT_MODEL = "small.en"


def write_wav(path: str, pcm: bytes, sample_rate: int) -> None:
    """Write PCM16 mono audio to a WAV file at the given path."""
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)


def _duration_s(pcm: bytes, sample_rate: int) -> float:
    """Duration of PCM16 mono audio in seconds."""
    if sample_rate <= 0:
        return 0.0
    return len(pcm) / (2.0 * sample_rate)


def _model_name(settings: Any) -> str:
    """Resolve the STT model name from settings (object, dict, or None).

    Falls back to DEFAULT_MODEL only when the settings carry no model
    name at all; a name that is present is used verbatim so a bad value
    fails validation instead of being silently replaced.
    """
    value = None
    if isinstance(settings, dict):
        value = settings.get("stt_model")
    elif settings is not None:
        value = getattr(settings, "stt_model", None)
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_MODEL
    return str(value).strip()


def _validate_model_name(name: str) -> str:
    """Enforce the English-only model pin and return the name unchanged.

    Accepts bare sizes ("small.en", "base.en"), hub ids ending in ".en"
    ("Systran/faster-whisper-small.en"), and local paths whose final
    component ends in ".en". Multilingual models ("small", "large-v3",
    "turbo") are rejected: with one of those, the English pin would rest
    on a decode-time flag alone instead of on the model itself.
    """
    final = name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if not final.endswith(".en"):
        raise ValueError(
            "STT model must be an English-only faster-whisper model "
            f"(name ending in .en, e.g. {DEFAULT_MODEL}); got {name!r}"
        )
    return name


def _check_language_info(info: Any) -> None:
    """Verify one transcription reports English at high probability.

    BUILD_PLAN Section 1.5: assert info.language == "en" and
    info.language_probability >= 0.9. This is an explicit raise, not a
    bare assert, so the check survives python -O. It fails closed: a
    transcription whose language cannot be proven English is an error,
    never a segment the gate might trust.

    With language="en" forced, faster-whisper reports language "en"
    with probability 1, so a failure here means the model or library
    did something the pinned contract does not allow.
    """
    language = getattr(info, "language", None)
    if language != LANGUAGE:
        raise RuntimeError(
            f"STT language check failed: expected {LANGUAGE!r}, "
            f"got {language!r}"
        )
    probability = getattr(info, "language_probability", None)
    try:
        probability = float(probability)
    except (TypeError, ValueError):
        probability = None
    if (
        probability is None
        or not math.isfinite(probability)
        or probability < MIN_LANGUAGE_PROBABILITY
    ):
        raise RuntimeError(
            "STT language check failed: expected language probability "
            f">= {MIN_LANGUAGE_PROBABILITY} for {LANGUAGE!r}, "
            f"got {probability!r}"
        )


class FasterWhisperTranscriber:
    """Real transcriber backed by faster-whisper (lazy-imported)."""

    def __init__(self, settings: Any) -> None:
        # Lazy import: only here, never at module import time, so the
        # package imports cleanly without faster-whisper installed.
        from faster_whisper import WhisperModel

        self.model_name = _validate_model_name(_model_name(settings))
        self._model = WhisperModel(
            self.model_name, device="cpu", compute_type=COMPUTE_TYPE
        )

    def transcribe_pcm(
        self, pcm: bytes, sample_rate: int = 16000
    ) -> list[RawSegment]:
        if _duration_s(pcm, sample_rate) < MIN_AUDIO_S:
            return []

        tmp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False
            ) as tmp:
                tmp_path = tmp.name
            write_wav(tmp_path, pcm, sample_rate)

            segments, info = self._model.transcribe(
                tmp_path,
                language=LANGUAGE,
                temperature=DECODE_TEMPERATURE,
                beam_size=DECODE_BEAM_SIZE,
                vad_filter=False,  # VAD stays OFF by design, see module docstring
                condition_on_previous_text=False,
                without_timestamps=False,
                word_timestamps=False,
            )
            _check_language_info(info)
            # segments is a generator; materialize it while the file exists.
            return [
                RawSegment(
                    text=seg.text,
                    avg_logprob=seg.avg_logprob,
                    no_speech_prob=seg.no_speech_prob,
                    start=seg.start,
                    end=seg.end,
                )
                for seg in segments
            ]
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


class FakeTranscriber:
    """Deterministic fake Transcriber for tests and offline smoke runs.

    This is a test double, not a real transcriber. By default every call
    returns one fixed segment spanning the whole audio. A script can be
    supplied to control what successive calls return:

    - script as a list of lists of RawSegment: one inner list per
      transcribe call, consumed in order.
    - script as a plain list of RawSegment: shorthand for a one-call
      script.

    Once the script is exhausted (or if none was given), calls fall back
    to the default fixed segment described above.

    The default (unscripted) path mirrors the real transcriber's
    contract for too-short audio: under MIN_AUDIO_S it returns no
    segments at all, instead of inventing speech for silence. Scripted
    calls return exactly what the script says; the script is explicit
    test intent and is never produced by the production factory.
    """

    def __init__(
        self,
        script: Optional[Sequence] = None,
    ) -> None:
        self._script: list[list[RawSegment]] = []
        if script:
            items = list(script)
            if all(isinstance(item, RawSegment) for item in items):
                # Plain list of segments: shorthand for a one-call script.
                self._script = [list(items)]  # type: ignore[arg-type]
            else:
                self._script = [list(call) for call in items]  # type: ignore[attr-defined]

    def transcribe_pcm(
        self, pcm: bytes, sample_rate: int = 16000
    ) -> list[RawSegment]:
        if self._script:
            return list(self._script.pop(0))
        if _duration_s(pcm, sample_rate) < MIN_AUDIO_S:
            return []
        return [
            RawSegment(
                text=FAKE_TEXT,
                avg_logprob=-0.2,
                no_speech_prob=0.05,
                start=0.0,
                end=_duration_s(pcm, sample_rate),
            )
        ]


def get_transcriber(settings: Any):
    """Return the transcriber for this process.

    The fake is reachable only through the explicit ONRECORD_STT=fake
    opt-in (tests and offline smoke runs), and using it prints a warning
    to stderr. Any other failure to build the real transcriber raises:
    silently substituting canned text for speech would put fabricated
    VERIFIED segments into judged artifacts, which is exactly what the
    confidence gate exists to prevent.
    """
    if os.environ.get("ONRECORD_STT", "").strip().lower() == "fake":
        print(
            "warning: ONRECORD_STT=fake is set; using FakeTranscriber, "
            "which returns canned text and is not real transcription",
            file=sys.stderr,
        )
        return FakeTranscriber()
    try:
        return FasterWhisperTranscriber(settings)
    except Exception as exc:
        raise RuntimeError(
            "could not load the real faster-whisper transcriber "
            f"({type(exc).__name__}: {exc}); refusing to fall back to "
            "FakeTranscriber, whose canned text would be stored and "
            "gated as if it were real speech. Fix the model load, or "
            "set ONRECORD_STT=fake explicitly for a smoke run."
        ) from exc


__all__ = [
    "write_wav",
    "FasterWhisperTranscriber",
    "FakeTranscriber",
    "get_transcriber",
    "LANGUAGE",
    "MIN_LANGUAGE_PROBABILITY",
    "DECODE_TEMPERATURE",
    "DECODE_BEAM_SIZE",
    "COMPUTE_TYPE",
    "DEFAULT_MODEL",
]
