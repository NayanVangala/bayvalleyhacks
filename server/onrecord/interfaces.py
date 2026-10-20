"""Shared contracts for the OnRecord server.

Every module codes against these protocols and dataclasses so components can
be built and tested independently. Heavy ML libraries (faster-whisper,
insightface, speechbrain, chromadb, sentence-transformers) are lazy-imported
inside their real implementations; lightweight fakes live next to them and
satisfy the same protocols for tests and offline smoke runs.

No em-dashes in this codebase. Keep comments plain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence

DIM_TEXT = 384
DIM_FACE = 512
DIM_VOICE = 192

COLLECTION_FACE = "face_prints"
COLLECTION_VOICE = "voice_prints"
COLLECTION_CHUNKS = "transcript_chunks"
COLLECTIONS = (COLLECTION_FACE, COLLECTION_VOICE, COLLECTION_CHUNKS)

# Segment gate statuses (BUILD_PLAN Section 2.5).
GATE_VERIFIED = "VERIFIED"
GATE_REVIEW = "REVIEW"
GATE_SILENCE = "SILENCE"

# Attribution sources (Revision 2 fusion + manual correction layer).
ATTR_FUSION = "FUSION_FACE_VOICE"
ATTR_FACE_ONLY = "FACE_ONLY"
ATTR_VOICE_ONLY = "VOICE_ONLY"
ATTR_MANUAL = "MANUAL"
ATTR_UNKNOWN = "UNKNOWN"

# Query no-silent-empty reason codes (BUILD_PLAN Section 2.6).
REASON_NO_MATCHES = "NO_MATCHES"
REASON_NO_VERIFIED = "NO_VERIFIED_SEGMENTS"
REASON_PERSON_NOT_FOUND = "PERSON_NOT_FOUND"
REASON_NO_PEOPLE = "NO_PEOPLE"


@dataclass
class RawSegment:
    """One transcribed piece of a sealed audio segment, from a Transcriber."""

    text: str
    avg_logprob: float
    no_speech_prob: float
    start: float  # seconds, offset within the sealed segment audio
    end: float  # seconds
    words: list = field(default_factory=list)  # optional word-level dicts


@dataclass
class SealedSegment:
    """Audio sealed by the session state machine, ready for STT."""

    seq: int
    pcm: bytes  # PCM16 mono, sample_rate from config (default 16000)
    duration_s: float
    preroll: bool = False


class Transcriber(Protocol):
    def transcribe_pcm(self, pcm: bytes, sample_rate: int = 16000) -> list[RawSegment]:
        ...


class TextEmbedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]:
        ...

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        ...


class FaceEmbedder(Protocol):
    dim: int

    def embed_image(self, image_bytes: bytes) -> Optional[list[float]]:
        """Return a face embedding, or None if no usable face is found."""
        ...


class VoiceEmbedder(Protocol):
    dim: int

    def embed_pcm(self, pcm: bytes, sample_rate: int = 16000) -> Optional[list[float]]:
        """Return a speaker embedding, or None if audio is unusable."""
        ...


@dataclass
class VectorHit:
    id: str
    score: float  # cosine similarity, higher is closer
    metadata: dict = field(default_factory=dict)
    document: Optional[str] = None


class VectorStore(Protocol):
    def upsert(
        self,
        collection: str,
        id: str,
        embedding: Sequence[float],
        metadata: dict,
        document: Optional[str] = None,
    ) -> None:
        ...

    def query(self, collection: str, embedding: Sequence[float], n: int = 5) -> list[VectorHit]:
        ...

    def get(self, collection: str, id: str) -> Optional[VectorHit]:
        ...

    def delete(self, collection: str, id: str) -> None:
        ...

    def count(self, collection: str) -> int:
        ...


@dataclass
class MatchResult:
    """Outcome of identity fusion for one segment."""

    person_id: Optional[str]  # None means Unknown, never a guess
    source: str  # one of the ATTR_* constants
    face_score: Optional[float] = None
    voice_score: Optional[float] = None
    face_person_id: Optional[str] = None
    voice_person_id: Optional[str] = None


@dataclass
class QueryHit:
    segment_id: str
    session_id: str
    speaker_name: str
    speaker_person_id: Optional[str]
    text: str  # verbatim quote from the stored segment
    start_iso: str  # session start + segment offset, ISO 8601
    end_iso: str
    score: Optional[float] = None  # similarity for semantic hits, None for exact


@dataclass
class QueryResult:
    """No-silent-empty contract: EMPTY always carries a reason code."""

    status: str  # "OK" or "EMPTY"
    reason: Optional[str]  # one of the REASON_* constants when EMPTY
    results: list[QueryHit] = field(default_factory=list)
    # For AMBIGUOUS_NAME only: the people the name could mean, as
    # {"id", "name"} dicts. Zero segments are returned in that case;
    # the caller picks a candidate by id instead of the server
    # guessing (BUILD_PLAN Section 2.6). Empty for every other result.
    candidates: list = field(default_factory=list)
