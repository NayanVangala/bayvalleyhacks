"""Lexia OCR package.

Exports the shared OCR contracts and the available engines, plus
get_engine() which picks an engine based on the LEXIA_OCR_ENGINE
environment variable and what is installed on the machine.
"""

from __future__ import annotations

import os
import shutil

from .base import LineBox, OCRResult, OCREngine, OCRUnavailable
from .fake import FakeOCREngine
from .tesseract import TesseractEngine

__all__ = [
    "LineBox",
    "OCRResult",
    "OCREngine",
    "OCRUnavailable",
    "FakeOCREngine",
    "TesseractEngine",
    "get_engine",
]


def get_engine() -> OCREngine:
    """Return the OCR engine selected for this environment.

    Selection rules, driven by the LEXIA_OCR_ENGINE variable:
    - "fake" returns a FakeOCREngine.
    - "tesseract" returns a TesseractEngine.
    - Unset (or any other value) returns a TesseractEngine when the
      tesseract binary is on PATH, otherwise a FakeOCREngine so the
      service still runs on machines without OCR support.
    """
    choice = os.environ.get("LEXIA_OCR_ENGINE", "").strip().lower()

    if choice == "fake":
        return FakeOCREngine()
    if choice == "tesseract":
        return TesseractEngine()

    if shutil.which("tesseract"):
        return TesseractEngine()
    return FakeOCREngine()
