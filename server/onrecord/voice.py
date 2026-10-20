"""Voice embeddings for OnRecord.

Provides a deterministic fake for tests and a real ECAPA-TDNN wrapper
based on SpeechBrain. Heavy libraries (speechbrain and torch) are
lazy-imported inside the real class only, so this module imports
cleanly with just the standard library.

The fake must never leak into production identity decisions by
accident: get_voice_embedder only falls back to it when the operator
explicitly opted in (ONRECORD_VOICE=fake,
ONRECORD_ALLOW_FAKE_EMBEDDERS=1, or ONRECORD_REHEARSAL=1). Otherwise a
missing speechbrain install raises, so a live run fails loudly instead
of matching voices with a hash.
"""

from __future__ import annotations

import hashlib
import math
import os
import sys
import tempfile
from typing import Optional

from .interfaces import DIM_VOICE

_MIN_FAKE_SECONDS = 0.3
_MIN_ECAPA_SECONDS = 0.5

_TRUTHY = frozenset({"1", "true", "yes"})


def fake_fallback_allowed() -> bool:
    """True only when the operator explicitly opted in to test doubles.

    Mirrors face.fake_fallback_allowed: ONRECORD_ALLOW_FAKE_EMBEDDERS=1
    for offline development, or ONRECORD_REHEARSAL=1 for the
    quarantined rehearsal fixture. With neither set, a missing real
    model is an error, never a silent downgrade for live identity.
    """
    for name in ("ONRECORD_ALLOW_FAKE_EMBEDDERS", "ONRECORD_REHEARSAL"):
        if os.environ.get(name, "").strip().lower() in _TRUTHY:
            return True
    return False



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

class FakeVoiceEmbedder:
    """Test double for VoiceEmbedder. NOT speaker recognition, tests only.

    This class does not perform speaker recognition at all. It maps the
    raw audio bytes to a deterministic pseudo-embedding so tests can
    check plumbing, storage, and cosine similarity behaviour. Identical
    audio gives an identical embedding (cosine similarity 1.0), and
    different audio almost always gives a different embedding, but the
    values carry no speaker information and must never be used for real
    identification.
    """

    dim = DIM_VOICE
    is_test_double = True

    def embed_pcm(
        self, pcm: bytes, sample_rate: int = 16000
    ) -> Optional[list[float]]:
        if not pcm or sample_rate <= 0:
            return None
        if len(pcm) < sample_rate * 2 * _MIN_FAKE_SECONDS:
            return None

        digest = hashlib.sha256(pcm).digest()
        values = _deterministic_values(digest, DIM_VOICE)
        normalized = _normalize(values)
        if all(v == 0.0 for v in normalized):
            return None
        return normalized


class ECAPAEmbedder:
    """Real voice embedder wrapping SpeechBrain ECAPA-TDNN.

    Uses speechbrain/spkrec-ecapa-voxceleb, which produces a 192-dim
    speaker embedding. SpeechBrain and torch are imported lazily in
    this class only, never at module import time.
    """

    dim = DIM_VOICE
    is_test_double = False

    def __init__(self) -> None:
        # Lazy imports: only reached when the real embedder is built.
        from speechbrain.inference.speaker import EncoderClassifier

        savedir = os.path.join(
            tempfile.gettempdir(), "onrecord_spkrec_ecapa_voxceleb"
        )
        self._classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=savedir,
        )

    def embed_pcm(
        self, pcm: bytes, sample_rate: int = 16000
    ) -> Optional[list[float]]:
        if not pcm or sample_rate <= 0:
            return None
        if len(pcm) < sample_rate * 2 * _MIN_ECAPA_SECONDS:
            return None

        # Lazy imports: only the real embedder needs torch and numpy.
        import numpy as np
        import torch

        # Build the waveform from the PCM bytes: float samples in the
        # range -1.0 to 1.0, shaped (batch, time) for encode_batch. A
        # trailing odd byte, if any, is dropped so the int16 decode
        # never sees a partial sample. encode_batch consumes the tensor
        # directly, so no temporary WAV file is written.
        even_pcm = pcm[: (len(pcm) // 2) * 2]
        samples = (
            np.frombuffer(even_pcm, dtype=np.int16).astype(np.float32)
            / 32768.0
        )
        waveform = torch.from_numpy(np.ascontiguousarray(samples))
        waveform = waveform.unsqueeze(0)

        raw = self._classifier.encode_batch(waveform)
        vec = _embedding_to_numpy(raw)

        vec = np.squeeze(np.asarray(vec, dtype=np.float64))
        vec = vec.reshape(-1)
        if vec.shape[0] != DIM_VOICE:
            raise ValueError(
                "ECAPA embedding has dim %d, expected %d"
                % (vec.shape[0], DIM_VOICE)
            )
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            return None
        vec = vec / norm
        return vec.tolist()


def _embedding_to_numpy(raw):
    """Convert an encode_batch result to a numpy array.

    Handles torch tensors (via detach, cpu, numpy) as well as plain
    numpy arrays and nested lists returned by test doubles.
    """
    import numpy as np

    obj = raw
    if hasattr(obj, "detach"):
        obj = obj.detach()
    if hasattr(obj, "cpu"):
        obj = obj.cpu()
    if hasattr(obj, "numpy"):
        obj = obj.numpy()
    return np.asarray(obj)


def get_voice_embedder():
    """Return the best available voice embedder.

    The environment variable ONRECORD_VOICE=fake forces the fake.
    Otherwise the real ECAPA embedder is tried first. If it cannot
    load, falling back to the fake would silently turn live identity
    decisions into hash matching, so the fallback happens only when
    the operator explicitly opted in to test doubles (see
    fake_fallback_allowed). Without that opt-in the failure is raised
    as a RuntimeError.
    """
    if os.environ.get("ONRECORD_VOICE", "").strip().lower() == "fake":
        return FakeVoiceEmbedder()
    try:
        return ECAPAEmbedder()
    except Exception as exc:  # speechbrain or torch missing, model load, etc.
        if not fake_fallback_allowed():
            raise RuntimeError(
                "ECAPAEmbedder unavailable (%s); refusing to fall back "
                "to FakeVoiceEmbedder for identity decisions. Install "
                "speechbrain, or set ONRECORD_VOICE=fake or "
                "ONRECORD_ALLOW_FAKE_EMBEDDERS=1 for an explicit "
                "offline test-double run." % (exc,)
            ) from exc
        print(
            "warning: ECAPAEmbedder unavailable (%s); "
            "falling back to FakeVoiceEmbedder (test doubles "
            "explicitly allowed)" % (exc,),
            file=sys.stderr,
        )
        return FakeVoiceEmbedder()
