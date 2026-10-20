"""Text embedders for OnRecord.

Provides HashingTextEmbedder as a deterministic, dependency-free test
double, and SentenceTransformerEmbedder as the real semantic embedder.
The real embedder lazy-imports sentence-transformers inside its class only,
so this module imports cleanly without that package installed.

No em-dashes in this codebase. Keep comments plain.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sys
from typing import Optional, Sequence

from .interfaces import DIM_TEXT

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class HashingTextEmbedder:
    """Deterministic, dependency-free test double for TextEmbedder.

    This is a test double, not a real semantic model. It is semantically
    weak but deterministic: identical text gives identical vectors, and
    texts that share tokens give positive cosine similarity.

    Method: tokenize to lowercase alphanumeric tokens, hash each token
    with md5, pick a bucket index in [0, DIM_TEXT) and a sign (+1/-1)
    from the hash bits, accumulate signed counts, then L2 normalize.
    If there are no tokens, returns the zero vector.
    """

    dim: int = DIM_TEXT

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * DIM_TEXT
        if not text:
            return vec
        tokens = _TOKEN_RE.findall(text.lower())
        if not tokens:
            return vec
        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            # Use hash bits for both bucket and sign. The full hash
            # integer decides the bucket so higher bits also contribute,
            # and a far away bit decides the sign.
            hash_int = int.from_bytes(digest, "little")
            bucket = hash_int % DIM_TEXT
            sign = 1.0 if ((hash_int >> 100) & 1) == 0 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return [0.0] * DIM_TEXT
        return [v / norm for v in vec]

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class SentenceTransformerEmbedder:
    """Real text embedder backed by sentence-transformers.

    Uses model all-MiniLM-L6-v2, which produces 384 dimensional vectors
    matching DIM_TEXT. The sentence_transformers import is lazy and lives
    only inside this class, so importing this module does not require it.
    """

    dim: int = DIM_TEXT

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        # Lazy import: only inside this class.
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    def embed(self, text: str) -> list[float]:
        result = self._model.encode(text, normalize_embeddings=True)
        # encode returns a numpy array for a single input; tolist gives
        # plain Python floats.
        if hasattr(result, "tolist"):
            return [float(v) for v in result.tolist()]
        return [float(v) for v in result]

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        text_list = list(texts)
        if not text_list:
            return []
        result = self._model.encode(text_list, normalize_embeddings=True)
        if hasattr(result, "tolist"):
            return [[float(v) for v in row] for row in result.tolist()]
        return [[float(v) for v in row] for row in result]


def get_text_embedder(settings=None):
    """Return a text embedder based on settings or environment.

    Default is HashingTextEmbedder so tests and offline runs never
    download models. If the environment variable ONRECORD_EMBEDDER is
    set to "st", try SentenceTransformerEmbedder and fall back to
    HashingTextEmbedder on any failure, with a warning to stderr.

    The optional settings argument is accepted for call-site compatibility.
    If it is a dict with an "embedder" key, or an object with an
    "embedder" attribute, that value is treated like the env var value.
    """
    requested: Optional[str] = None

    if settings is not None:
        try:
            if isinstance(settings, dict):
                value = settings.get("embedder") or settings.get("text_embedder")
                if value is not None:
                    requested = str(value)
            elif hasattr(settings, "embedder"):
                value = getattr(settings, "embedder")
                if value is not None:
                    requested = str(value)
            elif hasattr(settings, "text_embedder"):
                value = getattr(settings, "text_embedder")
                if value is not None:
                    requested = str(value)
        except Exception:
            requested = None

    if requested is None:
        requested = os.environ.get("ONRECORD_EMBEDDER", "")

    normalized = str(requested).strip().lower()

    if normalized == "st":
        try:
            return SentenceTransformerEmbedder()
        except Exception as exc:
            print(
                f"warning: failed to load SentenceTransformerEmbedder ({exc}); "
                "falling back to HashingTextEmbedder",
                file=sys.stderr,
            )
            return HashingTextEmbedder()

    return HashingTextEmbedder()


__all__ = [
    "HashingTextEmbedder",
    "SentenceTransformerEmbedder",
    "get_text_embedder",
]
