"""Deterministic fake OCR engine for Lexia Read Mode.

Used for demos, tests, and machines without a tesseract binary.
It ignores the image content (beyond requiring non empty bytes)
and always returns the same canned result, so downstream chunking
and text to speech code can be exercised end to end.
"""

from __future__ import annotations

from .base import LineBox, OCRResult

DEFAULT_TEXT = (
    "The quick brown fox jumps over the lazy dog. "
    "Reading aloud helps the words stick. "
    "Short lines make the page feel friendly."
)

_LINES = [
    "The quick brown fox jumps over the lazy dog.",
    "Reading aloud helps the words stick.",
    "Short lines make the page feel friendly.",
]

# Plausible stacked line boxes: x, y, w, h per line.
_BOXES = [
    (10, 10, 430, 20),
    (10, 40, 340, 20),
    (10, 70, 360, 20),
]


class FakeOCREngine:
    """OCR engine that returns a fixed, deterministic result."""

    name = "fake"

    def extract(self, image_bytes: bytes) -> OCRResult:
        if not image_bytes:
            raise ValueError("FakeOCREngine.extract requires non empty image_bytes")

        lines = [
            LineBox(
                text=text,
                x=x,
                y=y,
                w=w,
                h=h,
                confidence=0.99,
            )
            for text, (x, y, w, h) in zip(_LINES, _BOXES)
        ]

        return OCRResult(
            text=DEFAULT_TEXT,
            lines=lines,
            mean_confidence=0.99,
            engine=self.name,
        )
