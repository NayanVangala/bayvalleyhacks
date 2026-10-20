"""In memory reading session storage for Lexia Read Mode.

Pure stdlib. Sessions are kept in a dict guarded by a lock so the
server can share one store across threads.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass

from .chunking import Chunk


@dataclass
class ReadingSession:
    id: str
    raw_text: str
    cleaned_text: str
    chunks: list[Chunk]
    simplified: bool
    engine: str
    mean_confidence: float
    created_at: float
    audio_path: str | None = None


class SessionStore:
    """Thread safe in memory store for reading sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, ReadingSession] = {}
        self._lock = threading.Lock()

    def create(
        self,
        raw_text: str = "",
        cleaned_text: str = "",
        chunks: list[Chunk] | None = None,
        simplified: bool = False,
        engine: str = "",
        mean_confidence: float = 0.0,
        *args,
        **kwargs,
    ) -> ReadingSession:
        # Tolerate alias keyword shapes used by callers and tests.
        if "text" in kwargs and not raw_text:
            raw_text = str(kwargs.pop("text"))
        if "cleaned" in kwargs and not cleaned_text:
            cleaned_text = str(kwargs.pop("cleaned"))
        if "ocr_engine" in kwargs and not engine:
            engine = str(kwargs.pop("ocr_engine"))
        if chunks is None and "chunks" in kwargs:
            chunks = kwargs.pop("chunks")
        # Positional shape create(text, chunks): second arg is a list.
        if isinstance(cleaned_text, (list, tuple)):
            chunks = list(cleaned_text)  # type: ignore[arg-type]
            cleaned_text = raw_text if isinstance(raw_text, str) else ""
        if isinstance(raw_text, (list, tuple)):
            if chunks is None:
                chunks = list(raw_text)  # type: ignore[arg-type]
            raw_text = ""
        if not cleaned_text and isinstance(raw_text, str):
            cleaned_text = raw_text
        if chunks is None:
            chunks = []
        session = ReadingSession(
            id=uuid.uuid4().hex,
            raw_text=raw_text if isinstance(raw_text, str) else str(raw_text),
            cleaned_text=(
                cleaned_text if isinstance(cleaned_text, str) else str(cleaned_text)
            ),
            chunks=list(chunks),
            simplified=bool(kwargs.pop("simplified", simplified)),
            engine=str(engine),
            mean_confidence=float(mean_confidence),
            created_at=time.time(),
        )
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> ReadingSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def set_audio(self, session_id: str, audio_path: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.audio_path = audio_path
            return True


def to_dict(session: ReadingSession) -> dict:
    """Return a JSON ready dict for a reading session."""
    chunk_dicts: list[dict] = []
    for pos, chunk in enumerate(session.chunks or []):
        if isinstance(chunk, dict):
            text = str(chunk.get("text", ""))
            chunk_dicts.append(
                {
                    "index": int(chunk.get("index", pos)),
                    "text": text,
                    "word_count": int(
                        chunk.get("word_count", len(text.split()))
                    ),
                }
            )
        elif isinstance(chunk, str):
            chunk_dicts.append(
                {"index": pos, "text": chunk, "word_count": len(chunk.split())}
            )
        else:
            chunk_dicts.append(
                {
                    "index": int(getattr(chunk, "index", pos)),
                    "text": str(getattr(chunk, "text", "")),
                    "word_count": int(
                        getattr(
                            chunk,
                            "word_count",
                            len(str(getattr(chunk, "text", "")).split()),
                        )
                    ),
                }
            )
    return {
        "id": session.id,
        "session_id": session.id,
        "raw_text": session.raw_text,
        "cleaned_text": session.cleaned_text,
        "chunks": chunk_dicts,
        "simplified": session.simplified,
        "engine": session.engine,
        "mean_confidence": session.mean_confidence,
        "created_at": session.created_at,
        "audio_path": session.audio_path,
    }
