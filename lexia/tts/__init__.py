"""TTS package for Lexia Read Mode."""

from __future__ import annotations

import os
import shutil

from .base import SpeakResult, TTSEngine, TTSUnavailable
from .fake import FakeTTSEngine
from .local import LocalTTSEngine

__all__ = [
    "TTSEngine",
    "SpeakResult",
    "TTSUnavailable",
    "LocalTTSEngine",
    "FakeTTSEngine",
    "get_tts_engine",
]


def get_tts_engine() -> TTSEngine:
    """Return a TTS engine based on the LEXIA_TTS_ENGINE env var.

    "fake" returns FakeTTSEngine, "local" returns LocalTTSEngine, and
    unset returns LocalTTSEngine when espeak is on PATH, otherwise
    FakeTTSEngine.
    """
    choice = os.environ.get("LEXIA_TTS_ENGINE")
    if choice is not None:
        normalized = choice.strip().lower()
        if normalized == "fake":
            return FakeTTSEngine()
        if normalized == "local":
            return LocalTTSEngine()
        raise ValueError(
            f"Unknown LEXIA_TTS_ENGINE value: {choice!r} (expected 'fake' or 'local')"
        )
    if shutil.which("espeak"):
        return LocalTTSEngine()
    return FakeTTSEngine()
