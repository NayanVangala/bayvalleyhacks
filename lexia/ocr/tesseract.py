"""Tesseract backed OCR engine for Lexia Read Mode.

Wraps pytesseract. The tesseract binary and the Python packages are
imported lazily inside extract() so importing this module never
fails on a machine without OCR support installed. When the binary
or the packages are missing, extract() raises OCRUnavailable with
a clear message instead of crashing.
"""

from __future__ import annotations

import io
import shutil

from .base import LineBox, OCRResult, OCRUnavailable
from .preprocess import preprocess_image


class TesseractEngine:
    """OCR engine powered by the system tesseract binary."""

    name = "tesseract"

    def extract(self, image_bytes: bytes) -> OCRResult:
        if shutil.which("tesseract") is None:
            raise OCRUnavailable(
                "Tesseract OCR is unavailable: the 'tesseract' binary "
                "was not found on PATH. Install tesseract or set "
                "LEXIA_OCR_ENGINE=fake to use the fake engine."
            )

        try:
            import pytesseract
            from PIL import Image
        except Exception as exc:
            raise OCRUnavailable(
                "Tesseract OCR is unavailable: failed to import "
                f"pytesseract or Pillow ({exc}). Install the missing "
                "package or set LEXIA_OCR_ENGINE=fake to use the "
                "fake engine."
            ) from exc

        processed = preprocess_image(image_bytes)

        try:
            image = Image.open(io.BytesIO(processed))
            image.load()
        except Exception as exc:
            raise OCRUnavailable(
                f"Tesseract OCR could not open the supplied image ({exc})."
            ) from exc

        try:
            data = pytesseract.image_to_data(
                image, output_type=pytesseract.Output.DICT
            )
        except OCRUnavailable:
            raise
        except Exception as exc:
            raise OCRUnavailable(
                f"Tesseract OCR failed while reading the image ({exc})."
            ) from exc

        lines = self._group_words_into_lines(data)
        text = "\n".join(line.text for line in lines)
        if lines:
            mean_confidence = sum(line.confidence for line in lines) / len(lines)
        else:
            mean_confidence = 0.0

        return OCRResult(
            text=text,
            lines=lines,
            mean_confidence=mean_confidence,
            engine=self.name,
        )

    @staticmethod
    def _group_words_into_lines(data: dict) -> list[LineBox]:
        """Group word level tesseract output into line level boxes.

        Words are grouped by (block_num, par_num, line_num), keeping
        the order tesseract reported them in. Words with a negative
        confidence are structural rows, not real words, and are
        skipped, as are empty words.
        """
        count = len(data.get("text", []))
        grouped: dict[tuple, dict] = {}
        order: list[tuple] = []

        for index in range(count):
            word = str(data["text"][index]).strip()
            if not word:
                continue

            try:
                conf = float(data["conf"][index])
            except (TypeError, ValueError):
                continue
            if conf < 0:
                continue

            key = (
                data["block_num"][index],
                data["par_num"][index],
                data["line_num"][index],
            )
            left = int(data["left"][index])
            top = int(data["top"][index])
            width = int(data["width"][index])
            height = int(data["height"][index])

            if key not in grouped:
                grouped[key] = {
                    "words": [],
                    "confidences": [],
                    "x_min": left,
                    "y_min": top,
                    "x_max": left + width,
                    "y_max": top + height,
                }
                order.append(key)

            entry = grouped[key]
            entry["words"].append(word)
            entry["confidences"].append(conf / 100.0)
            entry["x_min"] = min(entry["x_min"], left)
            entry["y_min"] = min(entry["y_min"], top)
            entry["x_max"] = max(entry["x_max"], left + width)
            entry["y_max"] = max(entry["y_max"], top + height)

        lines: list[LineBox] = []
        for key in order:
            entry = grouped[key]
            confidences = entry["confidences"]
            confidence = sum(confidences) / len(confidences) if confidences else 0.0
            lines.append(
                LineBox(
                    text=" ".join(entry["words"]),
                    x=entry["x_min"],
                    y=entry["y_min"],
                    w=entry["x_max"] - entry["x_min"],
                    h=entry["y_max"] - entry["y_min"],
                    confidence=confidence,
                )
            )
        return lines
