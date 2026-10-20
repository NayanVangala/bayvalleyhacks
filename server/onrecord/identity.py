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

Two fail-closed rules sit on top of that:

- Ties never name anyone. If another enrolled person scores within a
  hair of the top hit in the same modality, that modality is ambiguous
  (for example the same photo enrolled for two people) and the result
  is Unknown rather than whichever print the store happened to return
  first.
- An embedding that is empty, all zero, the wrong dimension, or full
  of non-finite values counts as not provided, and a vector store
  failure counts as no hit. Identity problems degrade to Unknown,
  never to a crash and never to a guess.

Thresholds are cosine similarities: each one must be finite and inside
[0, 1]. A misconfigured threshold outside that range (a negative
threshold would name unrelated media) raises ValueError when the
matcher is built, instead of failing open at match time.

No ML libraries are imported here. Embedders and stores are injected
and satisfy the protocols in interfaces.py.
"""

from __future__ import annotations

import math
from typing import Optional

from .interfaces import (
    ATTR_FACE_ONLY,
    ATTR_FUSION,
    ATTR_UNKNOWN,
    ATTR_VOICE_ONLY,
    COLLECTION_CHUNKS,
    COLLECTION_FACE,
    COLLECTION_VOICE,
    DIM_FACE,
    DIM_VOICE,
    FaceEmbedder,
    MatchResult,
    VectorHit,
    VectorStore,
    VoiceEmbedder,
)

# Two prints for different people whose scores differ by less than
# this are a tie: the modality cannot tell them apart.
_TIE_EPSILON = 1e-6


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


def _usable_embedding(embedding) -> Optional[list]:
    """Return the embedding as a list of floats, or None if unusable.

    None, an empty sequence, an all zero vector, a value that cannot be
    read as floats, and any non-finite component all count as "not
    provided": a broken embedder output must degrade the same way a
    missing photo or a too short clip does, not block the other
    modality or crash the query.
    """
    if embedding is None:
        return None
    try:
        values = [float(v) for v in embedding]
    except (TypeError, ValueError):
        return None
    if not values:
        return None
    if not all(math.isfinite(v) for v in values):
        return None
    if all(v == 0.0 for v in values):
        return None
    return values


def _validate_embedding(embedding, expected_dim: int, kind: str, none_message: str):
    """Check an enrollment embedding and return it as a list of floats.

    Raises ValueError when the embedder produced nothing (using the
    caller supplied message), the wrong dimension for its collection,
    a non-finite component, or a zero vector. Storing any of those
    would corrupt the collection dimension or create a print that can
    never match honestly, so enrollment refuses them up front.
    """
    if embedding is None:
        raise ValueError(none_message)
    values = _usable_embedding(embedding)
    if values is None:
        raise ValueError("%s embedding is empty, zero, or non-finite" % kind)
    if len(values) != expected_dim:
        raise ValueError(
            "%s embedding has dim %d, expected %d"
            % (kind, len(values), expected_dim)
        )
    return values


def _top_hits(vectors: VectorStore, collection: str, embedding) -> list:
    """Query a collection for the top 2 hits; a store failure is no hits.

    Two hits are needed so the matcher can see a runner-up: with only
    the top hit, a tie between two people is invisible and whichever
    print the store returns first would win by accident.
    """
    if embedding is None:
        return []
    try:
        hits = vectors.query(collection, embedding, 2)
    except Exception:
        return []
    return list(hits) if hits else []


def _hit_score(hit: Optional[VectorHit]) -> Optional[float]:
    """Finite float score of a hit, or None if the score is unusable."""
    if hit is None:
        return None
    try:
        score = float(getattr(hit, "score"))
    except (TypeError, ValueError):
        return None
    return score if math.isfinite(score) else None


def _is_ambiguous(hits: list, top_person_id: Optional[str]) -> bool:
    """True when a different person ties the top hit in this modality."""
    if len(hits) < 2 or top_person_id is None:
        return False
    runner_up = hits[1]
    runner_up_id = _person_id_from_hit(runner_up)
    if runner_up_id is None or runner_up_id == top_person_id:
        return False
    top_score = _hit_score(hits[0])
    runner_up_score = _hit_score(runner_up)
    if top_score is None or runner_up_score is None:
        return False
    return runner_up_score >= top_score - _TIE_EPSILON


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

        Raises ValueError if the name is blank, the photo has no
        usable face, or the audio has no usable voice. Both embeddings
        are fully validated before any person is created. If storing
        the prints then fails partway, the prints stored so far and
        (when the store supports it) the person row are removed, so a
        failed enrollment never leaves a half enrolled person who
        could match on one modality alone.
        Returns the created person dict.
        """
        if not str(name or "").strip():
            raise ValueError("a name is required to enroll a person")
        face_embedding = _validate_embedding(
            self.face_embedder.embed_image(photo_bytes),
            DIM_FACE,
            "face",
            "no usable face in enrollment photo",
        )
        voice_embedding = _validate_embedding(
            self.voice_embedder.embed_pcm(voice_pcm),
            DIM_VOICE,
            "voice",
            "no usable voice in enrollment audio",
        )

        person = self.store.create_person(name, notes=notes)
        person_id = _person_id(person)
        if person_id is None:
            # Fall back to a direct dict access so a malformed store
            # fails loudly here instead of storing orphan prints.
            person_id = str(person["id"])

        metadata = {"person_id": person_id, "name": name}
        try:
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
        except Exception:
            self._rollback_enrollment(person_id)
            raise
        return person

    def _rollback_enrollment(self, person_id: str) -> None:
        """Best effort removal of a partially enrolled person.

        Deletes both prints (whether or not each upsert landed) and,
        when the store provides delete_person, the person row. Cleanup
        failures are swallowed so the original enrollment error is the
        one the caller sees; a print that survives cleanup is an
        orphan with no person row behind it, and delete_person on
        this service purges those too.
        """
        for collection, prefix in (
            (COLLECTION_FACE, "face:"),
            (COLLECTION_VOICE, "voice:"),
        ):
            try:
                self.vectors.delete(collection, prefix + person_id)
            except Exception:
                pass
        deleter = getattr(self.store, "delete_person", None)
        if callable(deleter):
            try:
                deleter(person_id)
            except Exception:
                pass

    def _require_existing_person(self, person_id: str):
        """Return the person for a print refresh, or raise KeyError.

        Raises KeyError only when the store can be asked and clearly
        has no such person: refreshing a print for a person id that
        does not exist would otherwise store an orphan print the
        matcher can name, attached to nobody. When the store cannot be
        asked, or the lookup itself fails, the refresh proceeds (the
        name lookup below already treats that as best effort).
        """
        get_person = getattr(self.store, "get_person", None)
        if get_person is None:
            return None
        try:
            person = get_person(person_id)
        except Exception:
            return None
        if person is None:
            raise KeyError(f"person not found: {person_id}")
        return person

    def enroll_face(self, person_id, photo_bytes):
        """Refresh the face print for an existing person.

        Raises ValueError if the photo has no usable face, and
        KeyError if the store has no such person.
        """
        face_embedding = _validate_embedding(
            self.face_embedder.embed_image(photo_bytes),
            DIM_FACE,
            "face",
            "no usable face in enrollment photo",
        )
        person_id = str(person_id)
        person = self._require_existing_person(person_id)
        metadata = {"person_id": person_id}
        name = _person_name(person) or self._lookup_name(person_id)
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

        Raises ValueError if the audio has no usable voice, and
        KeyError if the store has no such person.
        """
        voice_embedding = _validate_embedding(
            self.voice_embedder.embed_pcm(voice_pcm),
            DIM_VOICE,
            "voice",
            "no usable voice in enrollment audio",
        )
        person_id = str(person_id)
        person = self._require_existing_person(person_id)
        metadata = {"person_id": person_id}
        name = _person_name(person) or self._lookup_name(person_id)
        if name:
            metadata["name"] = name
        self.vectors.upsert(
            COLLECTION_VOICE,
            "voice:" + person_id,
            voice_embedding,
            metadata,
        )

    def delete_person(self, person_id):
        """Delete a person everywhere their data lives.

        This is the operation the enrollment consent script promises
        ("you can ask me to delete them at any time"). It removes the
        person row and their attributed segments via the store, then
        the face print, the voice print, and every transcript chunk
        for those segments from the vector store. The prints are
        deleted even when the store row is already gone, so an orphan
        print left by an earlier partial failure is still purged.
        Returns the store result dict, or None when the store has no
        such person and no store level delete was available.
        """
        person_id = str(person_id)

        segment_ids: list = []
        segments_for = getattr(self.store, "segments_for_person", None)
        if callable(segments_for):
            try:
                for seg in segments_for(person_id) or []:
                    seg_id = seg.get("id") if isinstance(seg, dict) else None
                    if seg_id is not None:
                        segment_ids.append(seg_id)
            except Exception:
                segment_ids = []

        result = None
        deleter = getattr(self.store, "delete_person", None)
        if callable(deleter):
            result = deleter(person_id)
            if isinstance(result, dict) and result.get("segment_ids"):
                segment_ids = list(result["segment_ids"])

        self.vectors.delete(COLLECTION_FACE, "face:" + person_id)
        self.vectors.delete(COLLECTION_VOICE, "voice:" + person_id)
        for segment_id in segment_ids:
            self.vectors.delete(COLLECTION_CHUNKS, segment_id)
        return result

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
        self.face_threshold = self._checked_threshold(
            settings, "face_threshold", 0.45
        )
        self.voice_threshold = self._checked_threshold(
            settings, "voice_threshold", 0.75
        )
        self.face_only_threshold = self._checked_threshold(
            settings, "face_only_threshold", 0.60
        )
        self.voice_only_threshold = self._checked_threshold(
            settings, "voice_only_threshold", 0.85
        )

    @staticmethod
    def _checked_threshold(settings, name: str, default: float) -> float:
        """Read one cosine threshold, refusing out of range values.

        A threshold below 0 fails open (even a zero or negative score
        would clear it, so unrelated media could be named), a NaN
        threshold silently disables matching, and a value above 1 can
        never be cleared. All three are configuration errors, so they
        raise ValueError here instead of distorting live decisions.
        """
        value = float(getattr(settings, name, default))
        if not math.isfinite(value) or value < 0.0 or value > 1.0:
            raise ValueError(
                "%s must be a cosine similarity in [0, 1], got %r"
                % (name, value)
            )
        return value

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

        In every path, a modality whose runner-up is a different
        person tied with the top hit is ambiguous and cannot name
        anyone, no matter how high the tied score is.
        """
        face_embedding = _usable_embedding(face_embedding)
        voice_embedding = _usable_embedding(voice_embedding)
        has_face = face_embedding is not None
        has_voice = voice_embedding is not None

        if not has_face and not has_voice:
            return MatchResult(person_id=None, source=ATTR_UNKNOWN)

        face_hits = (
            _top_hits(self.vectors, COLLECTION_FACE, face_embedding)
            if has_face
            else []
        )
        voice_hits = (
            _top_hits(self.vectors, COLLECTION_VOICE, voice_embedding)
            if has_voice
            else []
        )
        face_hit = face_hits[0] if face_hits else None
        voice_hit = voice_hits[0] if voice_hits else None

        face_person_id = _person_id_from_hit(face_hit)
        voice_person_id = _person_id_from_hit(voice_hit)
        face_score = _hit_score(face_hit)
        voice_score = _hit_score(voice_hit)
        face_ambiguous = _is_ambiguous(face_hits, face_person_id)
        voice_ambiguous = _is_ambiguous(voice_hits, voice_person_id)

        if has_face and has_voice:
            if (
                face_hit is not None
                and voice_hit is not None
                and not face_ambiguous
                and not voice_ambiguous
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
                and not face_ambiguous
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
            and not voice_ambiguous
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
        is treated as not provided. An embedder that raises is treated
        the same way: one dead modality degrades to the other (or to
        Unknown), matching the pipeline, instead of crashing the caller
        mid segment.
        """
        face_embedding = None
        if photo_bytes is not None and face_embedder is not None:
            try:
                face_embedding = face_embedder.embed_image(photo_bytes)
            except Exception:
                face_embedding = None
        voice_embedding = None
        if pcm is not None and voice_embedder is not None:
            try:
                voice_embedding = voice_embedder.embed_pcm(pcm)
            except Exception:
                voice_embedding = None
        return self.identify(
            face_embedding=face_embedding,
            voice_embedding=voice_embedding,
        )
