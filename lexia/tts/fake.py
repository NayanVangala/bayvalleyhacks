"""Deterministic fake TTS engine that writes real silent WAV files."""

from __future__ import annotations

import hashlib
import pathlib
import wave

from .base import SpeakResult

SAMPLE_RATE = 16000
MIN_DURATION = 0.5
MAX_DURATION = 60.0
WORDS_PER_SECOND = 2.6


class FakeTTSEngine:
    name = "fake"

    def synthesize(self, text: str, out_dir: pathlib.Path) -> SpeakResult:
        if not text or not text.strip():
            raise ValueError("FakeTTSEngine requires non-empty text")

        word_count = len(text.split())
        duration = word_count / WORDS_PER_SECOND
        duration = max(MIN_DURATION, min(MAX_DURATION, duration))

        out_dir = pathlib.Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        out_path = out_dir / f"fake_{digest}.wav"

        frame_count = int(duration * SAMPLE_RATE)
        silence_frame = b"\x00\x00"

        with wave.open(str(out_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(silence_frame * frame_count)

        actual_duration = frame_count / SAMPLE_RATE

        return SpeakResult(
            audio_path=str(out_path),
            duration_seconds=actual_duration,
            engine=self.name,
        )
