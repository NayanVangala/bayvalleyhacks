"""Vector stores for OnRecord.

Provides FakeVectorStore as a deterministic in-memory test double, and
ChromaVectorStore as the real persistent store backed by ChromaDB.
ChromaVectorStore lazy-imports chromadb inside its constructor only, so
this module imports cleanly without chromadb installed.

Collections (see interfaces.py): face_prints, voice_prints, and
transcript_chunks. All collections use cosine space, and scores are
cosine similarities where higher is closer.

No em-dashes in this codebase. Keep comments plain.
"""

from __future__ import annotations

import os
import sys
from typing import Optional, Sequence

import numpy as np

from .interfaces import COLLECTIONS, VectorHit


def _resolve_chroma_dir(settings) -> str:
    """Pull the Chroma directory out of a settings object or dict.

    Accepts an object with a chroma_dir attribute, a dict with a
    "chroma_dir" key, or a plain string or path. Falls back to a local
    "chroma_data" directory when nothing is configured.
    """
    if settings is None:
        return "chroma_data"
    if isinstance(settings, (str, os.PathLike)):
        return str(settings)
    if isinstance(settings, dict):
        value = settings.get("chroma_dir") or settings.get("chroma_path")
        return str(value) if value else "chroma_data"
    value = getattr(settings, "chroma_dir", None)
    if value is None:
        value = getattr(settings, "chroma_path", None)
    return str(value) if value else "chroma_data"


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two vectors. Zero vectors score 0.0."""
    va = np.asarray(a, dtype=float)
    vb = np.asarray(b, dtype=float)
    if va.size == 0 or vb.size == 0:
        return 0.0
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)


class FakeVectorStore:
    """In-memory VectorStore. This is a test double.

    Deterministic, dependency-light stand-in for ChromaVectorStore, for
    tests and offline runs. Nothing is persisted: data lives only as
    long as the instance. Storage shape is
    {collection: {id: (embedding, metadata, document)}}.

    Query results are sorted by score descending, with ties broken by
    id so ordering is fully deterministic.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, tuple[list[float], dict, Optional[str]]]] = {
            name: {} for name in COLLECTIONS
        }

    def _bucket(self, collection: str) -> dict:
        return self._data.setdefault(collection, {})

    def upsert(
        self,
        collection: str,
        id: str,
        embedding: Sequence[float],
        metadata: dict,
        document: Optional[str] = None,
    ) -> None:
        self._bucket(collection)[id] = (
            [float(v) for v in embedding],
            dict(metadata) if metadata else {},
            document,
        )

    def query(self, collection: str, embedding: Sequence[float], n: int = 5) -> list[VectorHit]:
        if n <= 0:
            return []
        bucket = self._bucket(collection)
        if not bucket:
            return []
        hits = [
            VectorHit(
                id=entry_id,
                score=_cosine_similarity(embedding, stored_embedding),
                metadata=dict(stored_metadata),
                document=stored_document,
            )
            for entry_id, (stored_embedding, stored_metadata, stored_document) in bucket.items()
        ]
        hits.sort(key=lambda hit: (-hit.score, hit.id))
        return hits[:n]

    def get(self, collection: str, id: str) -> Optional[VectorHit]:
        entry = self._bucket(collection).get(id)
        if entry is None:
            return None
        _embedding, metadata, document = entry
        return VectorHit(id=id, score=1.0, metadata=dict(metadata), document=document)

    def delete(self, collection: str, id: str) -> None:
        self._bucket(collection).pop(id, None)

    def count(self, collection: str) -> int:
        return len(self._bucket(collection))


class ChromaVectorStore:
    """Real VectorStore backed by a persistent ChromaDB client.

    chromadb is lazy-imported inside this constructor only. Collections
    are created on demand with cosine space, and Chroma distances are
    converted back to cosine scores as score = 1.0 - distance.
    """

    def __init__(self, settings=None) -> None:
        # Lazy import: only inside this class constructor.
        import chromadb

        self.chroma_dir = _resolve_chroma_dir(settings)
        self._client = chromadb.PersistentClient(path=str(self.chroma_dir))
        self._collections: dict = {}

    def _collection(self, name: str):
        cached = self._collections.get(name)
        if cached is not None:
            return cached
        collection = self._client.get_or_create_collection(
            name, metadata={"hnsw:space": "cosine"}
        )
        self._collections[name] = collection
        return collection

    def upsert(
        self,
        collection: str,
        id: str,
        embedding: Sequence[float],
        metadata: dict,
        document: Optional[str] = None,
    ) -> None:
        col = self._collection(collection)
        kwargs: dict = {
            "ids": [id],
            "embeddings": [[float(v) for v in embedding]],
        }
        if metadata:
            kwargs["metadatas"] = [dict(metadata)]
        if document is not None:
            kwargs["documents"] = [document]
        col.upsert(**kwargs)

    def query(self, collection: str, embedding: Sequence[float], n: int = 5) -> list[VectorHit]:
        if n <= 0:
            return []
        col = self._collection(collection)
        total = int(col.count())
        if total == 0:
            return []
        result = col.query(
            query_embeddings=[[float(v) for v in embedding]],
            n_results=min(n, total),
            include=["metadatas", "documents", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        hits: list[VectorHit] = []
        for idx, hit_id in enumerate(ids):
            distance = distances[idx] if idx < len(distances) and distances[idx] is not None else 0.0
            metadata = metadatas[idx] if idx < len(metadatas) and metadatas[idx] else {}
            document = documents[idx] if idx < len(documents) else None
            hits.append(
                VectorHit(
                    id=hit_id,
                    score=1.0 - float(distance),
                    metadata=dict(metadata),
                    document=document,
                )
            )
        return hits

    def get(self, collection: str, id: str) -> Optional[VectorHit]:
        col = self._collection(collection)
        result = col.get(
            ids=[id], include=["embeddings", "metadatas", "documents"]
        )
        ids = result.get("ids") or []
        if not ids:
            return None
        metadatas = result.get("metadatas") or []
        documents = result.get("documents") or []
        metadata = metadatas[0] if metadatas and metadatas[0] else {}
        document = documents[0] if documents else None
        return VectorHit(id=ids[0], score=1.0, metadata=dict(metadata), document=document)

    def delete(self, collection: str, id: str) -> None:
        col = self._collection(collection)
        col.delete(ids=[id])

    def count(self, collection: str) -> int:
        return int(self._collection(collection).count())


def get_vector_store(settings=None):
    """Factory: return a ChromaVectorStore, or a FakeVectorStore fallback.

    Returns FakeVectorStore when the environment variable
    ONRECORD_VECTOR is set to "fake", or when ChromaVectorStore cannot
    be constructed (for example chromadb is not installed). The
    fallback case prints a short warning to stderr.
    """
    if os.environ.get("ONRECORD_VECTOR", "").strip().lower() == "fake":
        print(
            "warning: ONRECORD_VECTOR=fake, using FakeVectorStore",
            file=sys.stderr,
        )
        return FakeVectorStore()
    try:
        return ChromaVectorStore(settings)
    except Exception as exc:
        print(
            f"warning: ChromaVectorStore unavailable ({exc}); "
            "falling back to FakeVectorStore",
            file=sys.stderr,
        )
        return FakeVectorStore()


__all__ = [
    "FakeVectorStore",
    "ChromaVectorStore",
    "get_vector_store",
]
