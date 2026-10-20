"""Image preprocessing for Lexia OCR.

Cleans up a raw image before it reaches an OCR engine: grayscale
conversion, contrast normalization, and a light deskew pass. The
goal is to help the engine, not to be perfect, so every step is
best effort. This function must never raise on odd input: on any
failure it returns the original bytes unchanged.
"""

from __future__ import annotations

import io


def _estimate_skew_angle(gray_image, max_angle: int = 5):
    """Estimate a coarse skew angle in degrees, or return 0.

    Tries small rotations on a downscaled copy and scores each by
    the variance of its horizontal projection profile. Text rows
    line up best at the correct angle, which makes that profile
    peaky and raises the variance. Returns 0 when the estimate does
    not clearly beat the unrotated image, so straight pages are
    left alone.
    """
    try:
        import math

        from PIL import Image

        probe = gray_image.copy()
        probe.thumbnail((800, 800), Image.LANCZOS)
        width, height = probe.size
        if width == 0 or height == 0:
            return 0

        def projection_variance(img) -> float:
            pixels = img.load()
            row_sums = []
            for y in range(img.size[1]):
                total = 0
                for x in range(img.size[0]):
                    # Dark pixels count as ink.
                    total += 255 - pixels[x, y]
                row_sums.append(total)
            if not row_sums:
                return 0.0
            mean = sum(row_sums) / len(row_sums)
            return sum((value - mean) ** 2 for value in row_sums) / len(row_sums)

        baseline = projection_variance(probe)
        if baseline <= 0:
            return 0

        best_angle = 0
        best_score = baseline
        for angle in range(-max_angle, max_angle + 1):
            if angle == 0:
                continue
            rotated = probe.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
            score = projection_variance(rotated)
            if score > best_score:
                best_score = score
                best_angle = angle

        # Only trust the estimate when it is a clear improvement.
        if best_angle != 0 and best_score > baseline * 1.05:
            # Guard against a nonsensical result.
            if math.isfinite(best_angle):
                return best_angle
        return 0
    except Exception:
        return 0


def preprocess_image(image_bytes: bytes) -> bytes:
    """Preprocess raw image bytes and return PNG bytes.

    Steps: open the image, convert to grayscale, apply autocontrast,
    and apply a coarse deskew rotation when a reliable skew estimate
    is found. On any failure the original bytes are returned as is.
    """
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(image_bytes)) as opened:
            image = opened.convert("L")

        image = ImageOps.autocontrast(image)

        angle = _estimate_skew_angle(image)
        if angle:
            image = image.rotate(angle, resample=Image.BICUBIC, fillcolor=255)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:
        return image_bytes
