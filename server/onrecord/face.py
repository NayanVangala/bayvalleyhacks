"""Face embedding for OnRecord: ArcFace via insightface, plus a fake.

The real implementation wraps insightface's FaceAnalysis (buffalo_l model
pack, ArcFace) and returns the 512-dim normed embedding of the largest
face in the image. insightface, cv2, and PIL are heavy and optional, so
they are lazy-imported inside InsightFaceEmbedder only; this module
imports cleanly with just the standard library.

FakeFaceEmbedder is the offline test double used when insightface is not
installed or ONRECORD_FACE=fake is set.

The fake must never leak into production identity decisions by accident:
get_face_embedder only falls back to it when the operator explicitly
opted in (ONRECORD_FACE=fake, ONRECORD_ALLOW_FAKE_EMBEDDERS=1, or
ONRECORD_REHEARSAL=1). Otherwise a missing insightface install raises,
so a live run fails loudly instead of matching faces with a hash.

No em-dashes in this codebase. Keep comments plain.
"""

from __future__ import annotations

import hashlib
import math
import os
import sys
from typing import Optional

from .interfaces import DIM_FACE

__all__ = [
    "deterministic_embedding",
    "fake_fallback_allowed",
    "FakeFaceEmbedder",
    "InsightFaceEmbedder",
    "get_face_embedder",
]



def _deterministic_values(seed_bytes: bytes, dim: int) -> list[float]:
    """Deterministic values in [-1, 1) from sha256(seed_bytes + counter).

    Pure python replacement for the old numpy RandomState expansion, so
    the fake embedders run with no third party dependencies at all.
    Identical seeds give identical values; unrelated seeds are near
    orthogonal after L2 normalization in high dimensions.
    """
    values: list[float] = []
    counter = 0
    while len(values) < dim:
        digest = hashlib.sha256(seed_bytes + counter.to_bytes(4, "little")).digest()
        for offset in range(0, len(digest), 4):
            if len(values) >= dim:
                break
            raw = int.from_bytes(digest[offset:offset + 4], "little")
            values.append((raw / 4294967296.0) * 2.0 - 1.0)
        counter += 1
    return values


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0.0:
        return [0.0] * len(values)
    return [v / norm for v in values]

def deterministic_embedding(seed_bytes: bytes, dim: int) -> list[float]:
    """Build a deterministic unit vector of length dim from seed_bytes.

    Values come from _deterministic_values (sha256 expansion, pure
    python), then L2 normalization, so the dot product of a vector
    with itself is 1.0 and unrelated seeds give near-orthogonal vectors.
    Same seed and dim always produce the exact same vector.
    """
    if dim <= 0:
        return []
    return _normalize(_deterministic_values(seed_bytes, dim))


_TRUTHY = frozenset({"1", "true", "yes"})


def fake_fallback_allowed() -> bool:
    """True only when the operator explicitly opted in to test doubles.

    Set ONRECORD_ALLOW_FAKE_EMBEDDERS=1 for offline development, or
    ONRECORD_REHEARSAL=1 for the quarantined rehearsal fixture. With
    neither set, a missing real model is an error, never a silent
    downgrade to hash matching for live identity decisions.
    """
    for name in ("ONRECORD_ALLOW_FAKE_EMBEDDERS", "ONRECORD_REHEARSAL"):
        if os.environ.get(name, "").strip().lower() in _TRUTHY:
            return True
    return False


class FakeFaceEmbedder:
    """Deterministic test double for FaceEmbedder. NOT face recognition.

    This double only matches identical inputs: the embedding is a hash
    expansion of the raw image bytes, so the exact same photo bytes give
    cosine 1.0 and any different bytes give a near-orthogonal vector. It
    cannot recognize the same person across two different photos, detect
    whether a face is present at all, or do anything a real face model
    does. It exists so tests and offline smoke runs can exercise the
    storage and matching plumbing without insightface installed.
    """

    dim = DIM_FACE
    is_test_double = True

    def embed_image(self, image_bytes: bytes) -> Optional[list[float]]:
        if not image_bytes:
            return None
        return deterministic_embedding(b"face:" + image_bytes, DIM_FACE)


class InsightFaceEmbedder:
    """Real ArcFace embedder backed by insightface (buffalo_l).

    insightface is lazy-imported in the constructor, so constructing this
    class is the point where a missing install raises; get_face_embedder
    handles that, falling back to FakeFaceEmbedder only when test
    doubles were explicitly allowed.
    """

    dim = DIM_FACE
    is_test_double = False

    def __init__(self) -> None:
        from insightface.app import FaceAnalysis

        self._app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        self._app.prepare(ctx_id=-1, det_size=(640, 640))

    @staticmethod
    def _decode_bgr(image_bytes: bytes):
        """Decode image bytes to a BGR numpy array, or None on failure."""
        import numpy as np

        try:
            import cv2
        except Exception:
            cv2 = None  # type: ignore[assignment]
        if cv2 is not None:
            try:
                buf = np.frombuffer(image_bytes, dtype=np.uint8)
                img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            except Exception:
                return None
            return img  # already BGR; None if the bytes were not an image
        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as im:
                rgb = np.asarray(im.convert("RGB"))
            return np.ascontiguousarray(rgb[:, :, ::-1])  # RGB to BGR
        except Exception:
            return None

    def embed_image(self, image_bytes: bytes) -> Optional[list[float]]:
        if not image_bytes:
            return None
        img = self._decode_bgr(image_bytes)
        if img is None:
            return None
        try:
            faces = self._app.get(img)
        except Exception:
            return None
        if not faces:
            return None

        import numpy as np

        def _area(face) -> float:
            bbox = face.bbox
            return float((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))

        best = max(faces, key=_area)
        emb = getattr(best, "normed_embedding", None)
        if emb is None:
            emb = getattr(best, "embedding", None)
            if emb is None:
                return None
        vec = np.asarray(emb, dtype=np.float64).ravel()
        # A print that is not exactly DIM_FACE long, finite, and nonzero
        # would corrupt or crash the face_prints collection (Chroma
        # infers the collection dimension from stored vectors), so it is
        # no usable face at all. Always return a unit vector so stored
        # prints are comparable no matter which attribute supplied it.
        if vec.size != DIM_FACE:
            return None
        if not np.all(np.isfinite(vec)):
            return None
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            return None
        vec = vec / norm
        return [float(x) for x in vec.tolist()]


def get_face_embedder():
    """Return the best available FaceEmbedder.

    ONRECORD_FACE=fake forces FakeFaceEmbedder. Otherwise try the real
    InsightFaceEmbedder. If the real model cannot load, falling back to
    the fake would silently turn live identity decisions into hash
    matching, so the fallback happens only when the operator explicitly
    opted in to test doubles (see fake_fallback_allowed). Without that
    opt-in the original failure is raised as a RuntimeError.
    """
    if os.environ.get("ONRECORD_FACE", "").strip().lower() == "fake":
        return FakeFaceEmbedder()
    try:
        return InsightFaceEmbedder()
    except Exception as exc:
        if not fake_fallback_allowed():
            raise RuntimeError(
                "InsightFaceEmbedder unavailable ("
                + str(exc)
                + "); refusing to fall back to FakeFaceEmbedder for "
                "identity decisions. Install insightface, or set "
                "ONRECORD_FACE=fake or ONRECORD_ALLOW_FAKE_EMBEDDERS=1 "
                "for an explicit offline test-double run."
            ) from exc
        print(
            "warning: InsightFaceEmbedder unavailable ("
            + str(exc)
            + "); falling back to FakeFaceEmbedder (test doubles "
            "explicitly allowed)",
            file=sys.stderr,
        )
        return FakeFaceEmbedder()
