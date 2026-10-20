"""Base contracts for Lexia TTS engines."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class SpeakResult:
    audio_path: str
    duration_seconds: float
    engine: str


class TTSUnavailable(Exception):
    """Raised when no usable TTS backend is available."""


@runtime_checkable
class TTSEngine(Protocol):
    name: str

    def synthesize(self, text: str, out_dir: pathlib.Path) -> SpeakResult:
        """Write an audio file into out_dir and return its result."""
        ...
