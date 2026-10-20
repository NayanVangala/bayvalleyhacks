"""Core types and protocol for Lexia OCR engines.

This module defines the shared data contracts every OCR engine in
Lexia Read Mode must return, plus the protocol engines implement.
It has no third party dependencies so it can be imported anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class LineBox:
    """A single recognized line of text with its bounding box.

    Coordinates are pixel positions in the source image: (x, y) is the
    top left corner, w and h are the width and height of the box.
    Confidence is normalized to the 0..1 range.
    """

    text: str
    x: int
    y: int
    w: int
    h: int
    confidence: float


@dataclass
class OCRResult:
    """Result of running an OCR engine over one image.

    text is the full recognized text with lines joined by newlines.
    lines holds one LineBox per recognized line, in reading order.
    mean_confidence is the mean of the line confidences in the 0..1
    range, or 0.0 when no lines were recognized.
    engine is the name of the engine that produced this result.
    """

    text: str
    lines: list[LineBox] = field(default_factory=list)
    mean_confidence: float = 0.0
    engine: str = ""


class OCRUnavailable(Exception):
    """Raised when an OCR engine cannot run in this environment.

    Typical causes: the underlying binary is not installed, or a
    required Python package failed to import.
    """


@runtime_checkable
class OCREngine(Protocol):
    """Protocol every Lexia OCR engine implements."""

    name: str

    def extract(self, image_bytes: bytes) -> OCRResult:
        """Run OCR over raw image bytes and return an OCRResult."""
        ...
