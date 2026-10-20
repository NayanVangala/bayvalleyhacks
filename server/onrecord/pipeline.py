"""Segment processing pipeline for OnRecord.

One sealed audio segment flows through, in order:

1. Identity embeddings (face from an optional photo, voice from the audio),
   with any embedder failure degrading to None instead of failing the segment.
2. Fusion matching against enrolled people. No match means Unknown, never
   a guess.
3. Speech to text, producing zero or more raw segments.
4. Confidence gating per raw segment (VERIFIED, REVIEW, or SILENCE).
5. Storage, then speaker attribution on every stored row.
6. Chunk embedding for semantic search.

Docstring rule, and the core honesty rule of this module: only VERIFIED
text is ever embedded for search; REVIEW text stays out of every judged
artifact. REVIEW and SILENCE rows are stored so a human can inspect them,
but they are never upserted into the chunk collection, never quoted by
search, and never counted as evidence.

Ordering note: a single sealed segment may yield multiple stored rows.
Every row from one sealed segment keeps seq set to sealed.seq, so seq alone
does not order rows within a sealed segment. Rows are returned in raw
segment order, and callers should order rows inside one sealed segment by
their start_offset_s values, which are strictly increasing in that order.
"""

from __future__ import annotations

from .gating import gate_segment
from .interfaces import (
    ATTR_UNKNOWN,
    COLLECTION_CHUNKS,
    GATE_SILENCE,
    GATE_VERIFIED,
    MatchResult,
    SealedSegment,
)

# Voice embeddings need a minimum amount of speech to be meaningful.
# Shorter clips skip the voice embedder instead of producing a noisy print.
MIN_VOICE_DURATION_S = 0.5

# PCM16 mono uses 2 bytes per sample. Used to cross check the declared
# duration against the actual buffer length when deciding whether the
# sealed segment is long enough for a voice embedding.
_BYTES_PER_SAMPLE = 2


def _row_id(row):
    """Return the stored segment id from a row dict.

    Rows normally carry the id under "id". Some store shapes use
    "segment_id" instead, so accept either rather than guessing blindly.
    """
    if isinstance(row, dict):
        if row.get("id") is not None:
            return row["id"]
        return row.get("segment_id")
    return None


def _row_text(row):
    """Return the display text stored on a row, or an empty string."""
    if isinstance(row, dict):
        return row.get("text") or ""
    return ""


def _row_gate_status(row):
    """Return the gate status stored on a row, or None."""
    if isinstance(row, dict):
        return row.get("gate_status")
    return None


class Pipeline:
    """Coordinate identity, STT, gating, storage, and chunk embedding."""

    def __init__(
        self,
        store,
        vectors,
        transcriber,
        face_embedder,
        voice_embedder,
        matcher,
        text_embedder,
        sample_rate=16000,
    ):
        self.store = store
        self.vectors = vectors
        self.transcriber = transcriber
        self.face_embedder = face_embedder
        self.voice_embedder = voice_embedder
        self.matcher = matcher
        self.text_embedder = text_embedder
        self.sample_rate = sample_rate

    # Identity embeddings -------------------------------------------------

    def _voice_embedding(self, sealed: SealedSegment):
        """Embed the sealed audio, or None if too short or on failure."""
        if self.voice_embedder is None:
            return None
        pcm = sealed.pcm or b""
        if not pcm:
            return None
        declared = sealed.duration_s or 0.0
        try:
            buffered = len(pcm) / float(_BYTES_PER_SAMPLE * self.sample_rate)
        except Exception:
            buffered = 0.0
        if max(declared, buffered) < MIN_VOICE_DURATION_S:
            return None
        try:
            return self.voice_embedder.embed_pcm(pcm, self.sample_rate)
        except Exception:
            return None

    def _face_embedding(self, photo_bytes):
        """Embed an optional photo, or None if absent or on failure."""
        if self.face_embedder is None:
            return None
        if not photo_bytes:
            return None
        try:
            return self.face_embedder.embed_image(photo_bytes)
        except Exception:
            return None

    # Speaker attribution --------------------------------------------------

    def _apply_speaker(self, rows, match):
        """Apply the fusion match to every stored row.

        Returns a tuple of the updated rows and the speaker label used.
        When there is no matched person, rows are left untouched as
        Unknown and the label is None.
        """
        person_id = getattr(match, "person_id", None) if match else None
        if not person_id:
            return rows, None

        label = None
        try:
            person = self.store.get_person(person_id)
        except Exception:
            person = None
        if isinstance(person, dict):
            label = person.get("name") or None
        if not label:
            label = "Unknown"

        source = getattr(match, "source", ATTR_UNKNOWN)
        face_score = getattr(match, "face_score", None)
        voice_score = getattr(match, "voice_score", None)

        updated_rows = []
        for row in rows:
            segment_id = _row_id(row)
            updated = self.store.set_segment_speaker(
                segment_id,
                person_id,
                label,
                source,
                face_score=face_score,
                voice_score=voice_score,
            )
            if isinstance(updated, dict) and updated:
                updated_rows.append(updated)
            else:
                updated_rows.append(row)
        return updated_rows, label

    # Chunk embedding ------------------------------------------------------

    def _embed_verified_chunks(self, rows, session_id, match, label):
        """Upsert VERIFIED, non-empty rows into the chunk collection.

        Only VERIFIED text is ever embedded for search; REVIEW text stays
        out of every judged artifact, and SILENCE rows carry no text.
        """
        person_id = getattr(match, "person_id", None) if match else None
        speaker_name = label or "Unknown"
        for row in rows:
            if _row_gate_status(row) != GATE_VERIFIED:
                continue
            text = _row_text(row)
            if not text:
                continue
            segment_id = _row_id(row)
            embedding = self.text_embedder.embed(text)
            metadata = {
                "segment_id": segment_id,
                "session_id": session_id,
                "person_id": person_id or "",
                "speaker_name": speaker_name,
                "gate_status": GATE_VERIFIED,
            }
            self.vectors.upsert(
                COLLECTION_CHUNKS,
                segment_id,
                embedding,
                metadata,
                document=text,
            )

    # Main entry point -----------------------------------------------------

    def process_segment(
        self,
        session_id,
        sealed: SealedSegment,
        base_offset_s=0.0,
        photo_bytes=None,
    ) -> list:
        """Process one sealed segment and return its stored rows in order.

        Args:
            session_id: Session the sealed segment belongs to.
            sealed: A SealedSegment with seq, pcm, and duration_s.
            base_offset_s: Offset of the sealed segment start within the
                session, in seconds. Raw segment offsets are relative to
                the sealed segment and are shifted by this value.
            photo_bytes: Optional photo taken during the segment, used
                for the face embedding. None means voice only matching.

        Returns:
            The stored segment dicts, in raw segment order. An empty
            transcription still returns one SILENCE row so the timeline
            has no silent gap.
        """
        voice_embedding = self._voice_embedding(sealed)
        face_embedding = self._face_embedding(photo_bytes)

        match = self.matcher.identify(
            face_embedding=face_embedding,
            voice_embedding=voice_embedding,
        )
        if match is None:
            match = MatchResult(person_id=None, source=ATTR_UNKNOWN)

        raw_segments = self.transcriber.transcribe_pcm(
            sealed.pcm, self.sample_rate
        )

        if not raw_segments:
            row = self.store.add_segment(
                session_id=session_id,
                seq=sealed.seq,
                text="",
                raw_text="",
                avg_logprob=None,
                no_speech_prob=None,
                gate_status=GATE_SILENCE,
                uncertain=False,
                start_offset_s=base_offset_s,
                end_offset_s=base_offset_s + sealed.duration_s,
            )
            rows, _label = self._apply_speaker([row], match)
            return rows

        rows = []
        for raw in raw_segments:
            gate = gate_segment(raw.avg_logprob, raw.no_speech_prob, raw.text)
            row = self.store.add_segment(
                session_id=session_id,
                seq=sealed.seq,
                text=gate.display_text,
                raw_text=raw.text,
                avg_logprob=raw.avg_logprob,
                no_speech_prob=raw.no_speech_prob,
                gate_status=gate.status,
                uncertain=gate.uncertain,
                start_offset_s=base_offset_s + raw.start,
                end_offset_s=base_offset_s + raw.end,
            )
            rows.append(row)

        rows, label = self._apply_speaker(rows, match)
        self._embed_verified_chunks(rows, session_id, match, label)
        return rows
