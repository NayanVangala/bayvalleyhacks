"""Speech-to-text for OnRecord, behind the Transcriber protocol.

The real implementation wraps faster-whisper. That library is heavy, so it
is lazy-imported inside the FasterWhisperTranscriber constructor only; this
module imports cleanly on machines without it.

FakeTranscriber is a deterministic test double, clearly labeled as a fake.
It exists for tests and offline smoke runs. It is not a real transcriber
and must never be presented as one; get_transcriber falls back to it only
when the real model cannot load, and it says so on stderr.

Design note: VAD stays OFF by design. The session state machine already
decides what counts as a sealed segment, so whisper-side voice activity
detection would only double-gate audio and risk dropping speech.
"""

from __future__ import annotations

import os
import sys
import tempfile
import wave
from typing import Any, Optional, Sequence

from .interfaces import RawSegment

# Audio shorter than this is not worth a model call.
MIN_AUDIO_S = 0.3

FAKE_TEXT = "this is a fake transcript segment"


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


class FasterWhisperTranscriber:
    """Real transcriber backed by faster-whisper (lazy-imported)."""

    def __init__(self, settings: Any) -> None:
        # Lazy import: only here, never at module import time, so the
        # package imports cleanly without faster-whisper installed.
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            settings.stt_model, device="cpu", compute_type="int8"
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

            segments, _info = self._model.transcribe(
                tmp_path,
                language="en",
                temperature=0,
                beam_size=5,
                vad_filter=False,  # VAD stays OFF by design, see module docstring
                condition_on_previous_text=False,
            )
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
    """Return the real transcriber, or the fake when it cannot load.

    Set ONRECORD_STT=fake to force the fake directly. Any load failure
    (ImportError included) prints a warning to stderr and falls back to
    FakeTranscriber so the server can still run for smoke tests.
    """
    if os.environ.get("ONRECORD_STT", "").strip().lower() == "fake":
        return FakeTranscriber()
    try:
        return FasterWhisperTranscriber(settings)
    except Exception as exc:  # ImportError or any other load failure
        print(
            "warning: could not load FasterWhisperTranscriber "
            f"({exc}); falling back to FakeTranscriber, "
            "which returns canned text and is not real transcription",
            file=sys.stderr,
        )
        return FakeTranscriber()


__all__ = [
    "write_wav",
    "FasterWhisperTranscriber",
    "FakeTranscriber",
    "get_transcriber",
]
