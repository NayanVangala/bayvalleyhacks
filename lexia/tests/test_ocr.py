"""Contract tests for lexia.ocr.

Contracts covered:
* FakeOCREngine().extract(b"img") returns a result with non-empty text,
  lines that each carry text and a numeric confidence, a
  mean_confidence consistent with the line confidences, and
  engine == "fake".
* Empty bytes raise ValueError.
* get_engine() respects the LEXIA_OCR_ENGINE environment variable.
* TesseractEngine raises OCRUnavailable when the tesseract binary is
  missing (skipped on machines that do have tesseract installed).
"""

import shutil

import pytest

from lexia.ocr import (
    FakeOCREngine,
    OCRUnavailable,
    TesseractEngine,
    get_engine,
)


def test_fake_engine_returns_text_lines_and_confidence():
    result = FakeOCREngine().extract(b"img")

    assert isinstance(result.text, str)
    assert result.text.strip() != ""

    assert result.lines, "fake OCR result should contain at least one line"
    for line in result.lines:
        assert isinstance(line.text, str)
        assert line.text.strip() != ""
        assert isinstance(line.confidence, (int, float))

    assert result.engine == "fake"


def test_fake_engine_mean_confidence_matches_lines():
    result = FakeOCREngine().extract(b"img")

    confidences = [line.confidence for line in result.lines]
    expected_mean = sum(confidences) / len(confidences)
    assert result.mean_confidence == pytest.approx(expected_mean)


def test_fake_engine_rejects_empty_bytes():
    with pytest.raises(ValueError):
        FakeOCREngine().extract(b"")


def test_get_engine_respects_env(monkeypatch):
    monkeypatch.setenv("LEXIA_OCR_ENGINE", "fake")
    engine = get_engine()
    assert isinstance(engine, FakeOCREngine) or getattr(
        engine, "name", getattr(engine, "engine", type(engine).__name__)
    ).lower() == "fake" or "fake" in type(engine).__name__.lower()


def test_tesseract_engine_unavailable_without_binary():
    if shutil.which("tesseract") is not None:
        pytest.skip("tesseract binary present on this machine")

    with pytest.raises(OCRUnavailable):
        engine = TesseractEngine()
        engine.extract(b"img")
