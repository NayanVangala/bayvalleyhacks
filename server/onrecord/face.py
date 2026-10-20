"""Face embedding for OnRecord: ArcFace via insightface, plus a fake.

The real implementation wraps insightface's FaceAnalysis (buffalo_l model
pack, ArcFace) and returns the 512-dim normed embedding of the largest
face in the image. insightface, cv2, and PIL are heavy and optional, so
they are lazy-imported inside InsightFaceEmbedder only; this module
imports cleanly with just the standard library and numpy.

FakeFaceEmbedder is the offline test double used when insightface is not
installed or ONRECORD_FACE=fake is set.

No em-dashes in this codebase. Keep comments plain.
"""

from __future__ import annotations

import hashlib
import os
import sys
from typing import Optional

import numpy as np

from .interfaces import DIM_FACE

__all__ = [
    "deterministic_embedding",
    "FakeFaceEmbedder",
    "InsightFaceEmbedder",
    "get_face_embedder",
]


def deterministic_embedding(seed_bytes: bytes, dim: int) -> list[float]:
    """Build a deterministic unit vector of length dim from seed_bytes.

    The sha256 digest of the seed is expanded with a counter: each block
    hashes seed_bytes plus a little-endian counter, seeds a numpy
    RandomState from the digest, and draws normal floats until dim values
    exist. The result is L2 normalized, so the dot product of a vector
    with itself is 1.0 and unrelated seeds give near-orthogonal vectors.
    Same seed and dim always produce the exact same vector.
    """
    if dim <= 0:
        return []
    values: list[float] = []
    counter = 0
    while len(values) < dim:
        digest = hashlib.sha256(seed_bytes + counter.to_bytes(4, "little")).digest()
        seed = int.from_bytes(digest[:4], "little")
        rng = np.random.RandomState(seed)
        take = min(128, dim - len(values))
        values.extend(rng.standard_normal(take).tolist())
        counter += 1
    vec = np.asarray(values[:dim], dtype=np.float64)
    norm = float(np.linalg.norm(vec))
    if norm == 0.0:
        return [0.0] * dim
    vec = vec / norm
    return [float(x) for x in vec.tolist()]


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

    def embed_image(self, image_bytes: bytes) -> Optional[list[float]]:
        if not image_bytes:
            return None
        return deterministic_embedding(b"face:" + image_bytes, DIM_FACE)


class InsightFaceEmbedder:
    """Real ArcFace embedder backed by insightface (buffalo_l).

    insightface is lazy-imported in the constructor, so constructing this
    class is the point where a missing install raises; get_face_embedder
    catches that and falls back to FakeFaceEmbedder.
    """

    dim = DIM_FACE

    def __init__(self) -> None:
        from insightface.app import FaceAnalysis

        self._app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        self._app.prepare(ctx_id=-1, det_size=(640, 640))

    @staticmethod
    def _decode_bgr(image_bytes: bytes) -> Optional[np.ndarray]:
        """Decode image bytes to a BGR numpy array, or None on failure."""
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

        def _area(face) -> float:
            bbox = face.bbox
            return float((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))

        best = max(faces, key=_area)
        emb = getattr(best, "normed_embedding", None)
        if emb is None:
            raw = getattr(best, "embedding", None)
            if raw is None:
                return None
            vec = np.asarray(raw, dtype=np.float64).ravel()
            norm = float(np.linalg.norm(vec))
            if norm == 0.0:
                return None
            emb = vec / norm
        vec = np.asarray(emb, dtype=np.float64).ravel()
        if vec.size == 0:
            return None
        return [float(x) for x in vec.tolist()]


def get_face_embedder():
    """Return the best available FaceEmbedder.

    ONRECORD_FACE=fake forces FakeFaceEmbedder. Otherwise try the real
    InsightFaceEmbedder; on any failure, warn on stderr and fall back to
    the fake so the server still runs offline.
    """
    if os.environ.get("ONRECORD_FACE", "").strip().lower() == "fake":
        return FakeFaceEmbedder()
    try:
        return InsightFaceEmbedder()
    except Exception as exc:
        print(
            "warning: InsightFaceEmbedder unavailable ("
            + str(exc)
            + "); falling back to FakeFaceEmbedder",
            file=sys.stderr,
        )
        return FakeFaceEmbedder()
