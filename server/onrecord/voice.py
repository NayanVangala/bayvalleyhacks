"""Voice embeddings for OnRecord.

Provides a deterministic fake for tests and a real ECAPA-TDNN wrapper
based on SpeechBrain. Heavy libraries (speechbrain and torch) are
lazy-imported inside the real class only, so this module imports
cleanly with just the standard library plus numpy.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import wave
from typing import Optional

import numpy as np

from .interfaces import DIM_VOICE

_MIN_FAKE_SECONDS = 0.3
_MIN_ECAPA_SECONDS = 0.5


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

    def embed_pcm(
        self, pcm: bytes, sample_rate: int = 16000
    ) -> Optional[list[float]]:
        if len(pcm) < sample_rate * 2 * _MIN_FAKE_SECONDS:
            return None

        digest = hashlib.sha256(pcm).digest()

        # Expand the 32 byte digest into DIM_VOICE values. Each block is
        # seeded from sha256(digest + counter) and drawn through numpy
        # RandomState, so the expansion is deterministic and depends on
        # every byte of the input audio.
        values: list[float] = []
        counter = 0
        while len(values) < DIM_VOICE:
            block_digest = hashlib.sha256(
                digest + counter.to_bytes(4, byteorder="little")
            ).digest()
            seed = int.from_bytes(block_digest[:4], byteorder="little")
            rng = np.random.RandomState(seed)
            needed = DIM_VOICE - len(values)
            values.extend(rng.standard_normal(needed).tolist())
            counter += 1

        vec = np.asarray(values[:DIM_VOICE], dtype=np.float64)
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            return None
        vec = vec / norm
        return vec.tolist()


class ECAPAEmbedder:
    """Real voice embedder wrapping SpeechBrain ECAPA-TDNN.

    Uses speechbrain/spkrec-ecapa-voxceleb, which produces a 192-dim
    speaker embedding. SpeechBrain and torch are imported lazily in
    this class only, never at module import time.
    """

    dim = DIM_VOICE

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
        if len(pcm) < sample_rate * 2 * _MIN_ECAPA_SECONDS:
            return None

        # Lazy import: only the real embedder needs torch.
        import torch

        tmp_path = None
        try:
            # Write a temporary WAV file (mono, 16-bit) as required.
            fd, tmp_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            with wave.open(tmp_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(pcm)

            # Build the waveform from the PCM bytes: float samples in
            # the range -1.0 to 1.0, shaped (batch, time) for
            # encode_batch. The WAV file above is the on-disk form of
            # exactly this waveform. A trailing odd byte, if any, is
            # dropped so the int16 decode never sees a partial sample.
            even_pcm = pcm[: (len(pcm) // 2) * 2]
            samples = (
                np.frombuffer(even_pcm, dtype=np.int16).astype(np.float32)
                / 32768.0
            )
            waveform = torch.from_numpy(np.ascontiguousarray(samples))
            waveform = waveform.unsqueeze(0)

            raw = self._classifier.encode_batch(waveform)
            vec = _embedding_to_numpy(raw)
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

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
    Otherwise the real ECAPA embedder is tried first, and any failure
    falls back to the fake with a warning on stderr.
    """
    if os.environ.get("ONRECORD_VOICE", "").strip().lower() == "fake":
        return FakeVoiceEmbedder()
    try:
        return ECAPAEmbedder()
    except Exception as exc:  # speechbrain or torch missing, model load, etc.
        print(
            "warning: ECAPAEmbedder unavailable (%s); "
            "falling back to FakeVoiceEmbedder" % (exc,),
            file=sys.stderr,
        )
        return FakeVoiceEmbedder()
