"""Enrollment and identity fusion for OnRecord.

Enrollment stores one face print and one voice print per person in the
vector store, keyed by person id.

Fusion rule: the system never guesses a name it is not sure about;
disagreement means Unknown. When both a face embedding and a voice
embedding are provided, a name is returned only if the top face hit and
the top voice hit agree on the same person and both scores clear their
fusion thresholds. If they disagree, either hit is missing, or either
score is too low, the result is Unknown and the per-modality person ids
and scores are recorded on the result for inspection. Single-modality
identification uses its own stricter threshold and is also Unknown
below that threshold.

No ML libraries are imported here. Embedders and stores are injected
and satisfy the protocols in interfaces.py.
"""

from __future__ import annotations

from typing import Optional

from .interfaces import (
    ATTR_FACE_ONLY,
    ATTR_FUSION,
    ATTR_UNKNOWN,
    ATTR_VOICE_ONLY,
    COLLECTION_FACE,
    COLLECTION_VOICE,
    FaceEmbedder,
    MatchResult,
    VectorHit,
    VectorStore,
    VoiceEmbedder,
)


def _person_id_from_hit(hit: Optional[VectorHit]) -> Optional[str]:
    """Extract the person id from a vector hit, or None if unavailable."""
    if hit is None:
        return None
    metadata = getattr(hit, "metadata", None) or {}
    if isinstance(metadata, dict):
        person_id = metadata.get("person_id")
        if person_id:
            return str(person_id)
    hit_id = getattr(hit, "id", None)
    if isinstance(hit_id, str) and ":" in hit_id:
        suffix = hit_id.split(":", 1)[1]
        if suffix:
            return suffix
    return None


def _top_hit(vectors: VectorStore, collection: str, embedding) -> Optional[VectorHit]:
    """Query a collection for the top 1 hit, or None if there is none."""
    if embedding is None:
        return None
    hits = vectors.query(collection, embedding, 1)
    if not hits:
        return None
    return hits[0]


def _person_name(person) -> Optional[str]:
    """Read the name field from a person dict or object, if present."""
    if person is None:
        return None
    if isinstance(person, dict):
        name = person.get("name")
        return str(name) if name else None
    name = getattr(person, "name", None)
    return str(name) if name else None


def _person_id(person) -> Optional[str]:
    """Read the id field from a person dict or object, if present."""
    if person is None:
        return None
    if isinstance(person, dict):
        pid = person.get("id")
        return str(pid) if pid is not None else None
    pid = getattr(person, "id", None)
    return str(pid) if pid is not None else None


class EnrollmentService:
    """Creates people and stores their face and voice prints."""

    def __init__(self, store, vectors, face_embedder, voice_embedder):
        self.store = store
        self.vectors = vectors
        self.face_embedder: FaceEmbedder = face_embedder
        self.voice_embedder: VoiceEmbedder = voice_embedder

    def enroll_person(self, name, photo_bytes, voice_pcm, notes=""):
        """Enroll a new person from a photo and a voice sample.

        Raises ValueError if the photo has no usable face or the audio
        has no usable voice. On failure no person is created.
        Returns the created person dict.
        """
        face_embedding = self.face_embedder.embed_image(photo_bytes)
        if face_embedding is None:
            raise ValueError("no usable face in enrollment photo")
        voice_embedding = self.voice_embedder.embed_pcm(voice_pcm)
        if voice_embedding is None:
            raise ValueError("no usable voice in enrollment audio")

        person = self.store.create_person(name, notes=notes)
        person_id = _person_id(person)
        if person_id is None:
            # Fall back to a direct dict access so a malformed store
            # fails loudly here instead of storing orphan prints.
            person_id = str(person["id"])

        metadata = {"person_id": person_id, "name": name}
        self.vectors.upsert(
            COLLECTION_FACE,
            "face:" + person_id,
            face_embedding,
            dict(metadata),
        )
        self.vectors.upsert(
            COLLECTION_VOICE,
            "voice:" + person_id,
            voice_embedding,
            dict(metadata),
        )
        return person

    def enroll_face(self, person_id, photo_bytes):
        """Refresh the face print for an existing person.

        Raises ValueError if the photo has no usable face.
        """
        face_embedding = self.face_embedder.embed_image(photo_bytes)
        if face_embedding is None:
            raise ValueError("no usable face in enrollment photo")
        person_id = str(person_id)
        metadata = {"person_id": person_id}
        name = self._lookup_name(person_id)
        if name:
            metadata["name"] = name
        self.vectors.upsert(
            COLLECTION_FACE,
            "face:" + person_id,
            face_embedding,
            metadata,
        )

    def enroll_voice(self, person_id, voice_pcm):
        """Refresh the voice print for an existing person.

        Raises ValueError if the audio has no usable voice.
        """
        voice_embedding = self.voice_embedder.embed_pcm(voice_pcm)
        if voice_embedding is None:
            raise ValueError("no usable voice in enrollment audio")
        person_id = str(person_id)
        metadata = {"person_id": person_id}
        name = self._lookup_name(person_id)
        if name:
            metadata["name"] = name
        self.vectors.upsert(
            COLLECTION_VOICE,
            "voice:" + person_id,
            voice_embedding,
            metadata,
        )

    def _lookup_name(self, person_id: str) -> Optional[str]:
        """Best effort name lookup for an existing person."""
        get_person = getattr(self.store, "get_person", None)
        if get_person is None:
            return None
        try:
            person = get_person(person_id)
        except Exception:
            return None
        return _person_name(person)


class FusionMatcher:
    """Matches face and voice embeddings against enrolled prints.

    The system never guesses a name it is not sure about; disagreement
    means Unknown.
    """

    def __init__(self, vectors, settings):
        self.vectors: VectorStore = vectors
        self.settings = settings
        self.face_threshold = float(getattr(settings, "face_threshold", 0.45))
        self.voice_threshold = float(getattr(settings, "voice_threshold", 0.75))
        self.face_only_threshold = float(
            getattr(settings, "face_only_threshold", 0.60)
        )
        self.voice_only_threshold = float(
            getattr(settings, "voice_only_threshold", 0.85)
        )

    def identify(self, face_embedding=None, voice_embedding=None) -> MatchResult:
        """Identify a person from a face embedding, a voice embedding, or both.

        Both provided: a name is returned with source ATTR_FUSION only
        when both top hits exist, agree on the same person, and clear
        face_threshold and voice_threshold. Otherwise the result is
        Unknown, with face_person_id, voice_person_id, and both scores
        recorded on the result.
        Only face: ATTR_FACE_ONLY when the top score clears
        face_only_threshold, else Unknown.
        Only voice: ATTR_VOICE_ONLY when the top score clears
        voice_only_threshold, else Unknown.
        Neither: Unknown.
        """
        has_face = face_embedding is not None
        has_voice = voice_embedding is not None

        if not has_face and not has_voice:
            return MatchResult(person_id=None, source=ATTR_UNKNOWN)

        face_hit = (
            _top_hit(self.vectors, COLLECTION_FACE, face_embedding)
            if has_face
            else None
        )
        voice_hit = (
            _top_hit(self.vectors, COLLECTION_VOICE, voice_embedding)
            if has_voice
            else None
        )

        face_person_id = _person_id_from_hit(face_hit)
        voice_person_id = _person_id_from_hit(voice_hit)
        face_score = float(face_hit.score) if face_hit is not None else None
        voice_score = float(voice_hit.score) if voice_hit is not None else None

        if has_face and has_voice:
            if (
                face_hit is not None
                and voice_hit is not None
                and face_person_id is not None
                and face_person_id == voice_person_id
                and face_score is not None
                and face_score >= self.face_threshold
                and voice_score is not None
                and voice_score >= self.voice_threshold
            ):
                return MatchResult(
                    person_id=face_person_id,
                    source=ATTR_FUSION,
                    face_score=face_score,
                    voice_score=voice_score,
                    face_person_id=face_person_id,
                    voice_person_id=voice_person_id,
                )
            return MatchResult(
                person_id=None,
                source=ATTR_UNKNOWN,
                face_score=face_score,
                voice_score=voice_score,
                face_person_id=face_person_id,
                voice_person_id=voice_person_id,
            )

        if has_face:
            if (
                face_hit is not None
                and face_person_id is not None
                and face_score is not None
                and face_score >= self.face_only_threshold
            ):
                return MatchResult(
                    person_id=face_person_id,
                    source=ATTR_FACE_ONLY,
                    face_score=face_score,
                    voice_score=None,
                    face_person_id=face_person_id,
                    voice_person_id=None,
                )
            return MatchResult(
                person_id=None,
                source=ATTR_UNKNOWN,
                face_score=face_score,
                voice_score=None,
                face_person_id=face_person_id,
                voice_person_id=None,
            )

        # Only voice.
        if (
            voice_hit is not None
            and voice_person_id is not None
            and voice_score is not None
            and voice_score >= self.voice_only_threshold
        ):
            return MatchResult(
                person_id=voice_person_id,
                source=ATTR_VOICE_ONLY,
                face_score=None,
                voice_score=voice_score,
                face_person_id=None,
                voice_person_id=voice_person_id,
            )
        return MatchResult(
            person_id=None,
            source=ATTR_UNKNOWN,
            face_score=None,
            voice_score=voice_score,
            face_person_id=None,
            voice_person_id=voice_person_id,
        )

    def identify_from_media(self, photo_bytes, pcm, face_embedder, voice_embedder) -> MatchResult:
        """Embed a photo and audio, then identify from both embeddings.

        Either input may be None; an input whose embedder returns None
        is treated as not provided.
        """
        face_embedding = None
        if photo_bytes is not None and face_embedder is not None:
            face_embedding = face_embedder.embed_image(photo_bytes)
        voice_embedding = None
        if pcm is not None and voice_embedder is not None:
            voice_embedding = voice_embedder.embed_pcm(pcm)
        return self.identify(
            face_embedding=face_embedding,
            voice_embedding=voice_embedding,
        )
