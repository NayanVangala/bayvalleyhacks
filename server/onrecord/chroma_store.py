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

import json
import math
import os
import sys
import threading
from pathlib import Path
from typing import Optional, Sequence

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
    """Cosine similarity between two vectors. Zero vectors score 0.0.

    Pure python: this module must import and run without numpy, which is
    not a core dependency (see server/requirements.txt).
    """
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        # A dimension mismatch is a storage bug upstream (for example
        # a voice print queried against the face collection). zip
        # would silently score the shared prefix, and two vectors
        # that agree on that prefix would score a confident 1.0 for
        # different speakers. Chroma raises on mismatched dimensions;
        # the fake must not invent a match instead.
        return 0.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    norm_a = math.sqrt(sum(float(x) * float(x) for x in a))
    norm_b = math.sqrt(sum(float(y) * float(y) for y in b))
    denom = norm_a * norm_b
    if denom == 0.0:
        return 0.0
    return float(dot / denom)


class FakeVectorStore:
    """VectorStore test double, in memory by default.

    Deterministic, dependency-light stand-in for ChromaVectorStore, for
    tests and offline runs. With no storage_path, data lives only as
    long as the instance. With a storage_path (used by get_vector_store
    for the fallback), data persists to that JSON file so separate
    processes share it. Storage shape is
    {collection: {id: (embedding, metadata, document)}}.

    Query results are sorted by score descending, with ties broken by
    id so ordering is fully deterministic.
    """

    def __init__(self, storage_path=None) -> None:
        # storage_path is optional. When given, the store persists to a
        # JSON file at that path and reloads it when another process
        # changes it, so separate processes (seed, server, MCP) share one
        # store on disk. With no path the store is purely in memory, which
        # is what unit tests want.
        self._storage_path = Path(storage_path) if storage_path else None
        self._lock = threading.RLock()
        self._loaded_stamp = None
        self._data: dict[str, dict[str, tuple[list[float], dict, Optional[str]]]] = {
            name: {} for name in COLLECTIONS
        }
        if self._storage_path is not None:
            self._load_from_disk(force=True)

    def _disk_stamp(self):
        if self._storage_path is None:
            return None
        try:
            st = self._storage_path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _load_from_disk(self, force=False) -> None:
        if self._storage_path is None:
            return
        stamp = self._disk_stamp()
        if not force and stamp == self._loaded_stamp:
            return
        self._loaded_stamp = stamp
        if stamp is None:
            return
        try:
            raw = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except Exception:
            return
        data = {name: {} for name in COLLECTIONS}
        for collection, entries in (raw or {}).items():
            bucket = data.setdefault(collection, {})
            for entry_id, item in (entries or {}).items():
                embedding, metadata, document = item
                bucket[entry_id] = (
                    [float(v) for v in embedding],
                    dict(metadata) if metadata else {},
                    document,
                )
        self._data = data

    def _save_to_disk(self) -> None:
        if self._storage_path is None:
            return
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            raw = {
                collection: {
                    entry_id: [list(embedding), dict(metadata), document]
                    for entry_id, (embedding, metadata, document) in entries.items()
                }
                for collection, entries in self._data.items()
            }
            tmp = self._storage_path.with_suffix(self._storage_path.suffix + ".tmp")
            tmp.write_text(json.dumps(raw), encoding="utf-8")
            tmp.replace(self._storage_path)
            self._loaded_stamp = self._disk_stamp()
        except Exception:
            pass

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
        with self._lock:
            self._load_from_disk()
            self._bucket(collection)[id] = (
                [float(v) for v in embedding],
                dict(metadata) if metadata else {},
                document,
            )
            self._save_to_disk()

    def query(self, collection: str, embedding: Sequence[float], n: int = 5) -> list[VectorHit]:
        # Reads take the lock too: an upsert on another thread mutates
        # the bucket dicts, and iterating one mid-mutation can raise
        # or tear a result list.
        with self._lock:
            self._load_from_disk()
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
        with self._lock:
            self._load_from_disk()
            entry = self._bucket(collection).get(id)
            if entry is None:
                return None
            _embedding, metadata, document = entry
            return VectorHit(id=id, score=1.0, metadata=dict(metadata), document=document)

    def delete(self, collection: str, id: str) -> None:
        with self._lock:
            self._load_from_disk()
            self._bucket(collection).pop(id, None)
            self._save_to_disk()

    def count(self, collection: str) -> int:
        with self._lock:
            self._load_from_disk()
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
        # get_or_create returns an existing collection as-is, even
        # when cosine metadata is requested here (verified against
        # chromadb 1.5: it hands back the existing collection without
        # a word). If that collection was created earlier with the
        # Chroma default space (l2), every distance it returns would
        # be converted with score = 1.0 - distance as if it were
        # cosine, producing wrong similarity scores with no error
        # anywhere. Fail loudly instead of misidentifying speakers
        # quietly. The space is recorded in different places across
        # chromadb versions: the collection metadata on older ones,
        # the configuration on newer ones, so check both.
        space = None
        try:
            space = (collection.metadata or {}).get("hnsw:space")
        except Exception:
            space = None
        if space is None:
            for attr in ("configuration_json", "configuration"):
                try:
                    config = getattr(collection, attr, None)
                    if isinstance(config, dict):
                        hnsw = config.get("hnsw") or {}
                        if isinstance(hnsw, dict) and hnsw.get("space"):
                            space = hnsw["space"]
                            break
                except Exception:
                    continue
        if space is not None and space != "cosine":
            raise ValueError(
                f"Chroma collection {name!r} uses space {space!r}, "
                "expected 'cosine'; delete and recreate the collection"
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
            # Chroma metadata values must be str, int, float, or bool;
            # a None value makes the whole upsert raise inside
            # chromadb. Drop None entries at this boundary instead
            # (a missing key reads back the same as no key).
            cleaned = {
                key: value
                for key, value in dict(metadata).items()
                if value is not None
            }
            if cleaned:
                kwargs["metadatas"] = [cleaned]
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
    # Persistent file for the fake fallback, under the chroma dir, so
    # vectors written by one process (seed) are visible to the next
    # (server, MCP). Without this, seeded chunks vanished at process exit
    # and semantic search always came back empty in a multi-process run.
    fake_path = Path(_resolve_chroma_dir(settings)) / "fake_vectors.json"
    if os.environ.get("ONRECORD_VECTOR", "").strip().lower() == "fake":
        print(
            "warning: ONRECORD_VECTOR=fake, using FakeVectorStore",
            file=sys.stderr,
        )
        return FakeVectorStore(fake_path)
    try:
        return ChromaVectorStore(settings)
    except Exception as exc:
        print(
            f"warning: ChromaVectorStore unavailable ({exc}); "
            "falling back to persistent FakeVectorStore",
            file=sys.stderr,
        )
        return FakeVectorStore(fake_path)


__all__ = [
    "FakeVectorStore",
    "ChromaVectorStore",
    "get_vector_store",
]
