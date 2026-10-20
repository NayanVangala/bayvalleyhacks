"""Query service for OnRecord.

Retrieval never guesses; empty results always carry a reason code;
raw_text never leaves the server.

Semantic search runs over the transcript chunk collection, exact search is
the backup path over the store, and both paths only ever surface segments
whose gate status is VERIFIED. A segment attributed to the wrong person, or
to nobody with confidence, is never presented as a verbatim quote: hits are
built from the stored segment row, not from vector metadata, and the stored
display text is quoted as-is.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Optional

from .interfaces import (
    COLLECTION_CHUNKS,
    GATE_VERIFIED,
    REASON_NO_MATCHES,
    REASON_NO_PEOPLE,
    REASON_NO_VERIFIED,
    REASON_PERSON_NOT_FOUND,
    QueryHit,
    QueryResult,
)

# Local reason code: the shared interfaces module has no constant for a name
# that matches more than one enrolled person, and resolving it by guessing
# would break the no-guessing rule, so ambiguous names are reported, not
# resolved.
REASON_AMBIGUOUS_NAME = "AMBIGUOUS_NAME"
AMBIGUOUS_NAME = REASON_AMBIGUOUS_NAME

STATUS_OK = "OK"
STATUS_EMPTY = "EMPTY"

# Hard ceiling on search results. Callers are local tools, so a larger
# limit is never legitimate; an unbounded limit lets one request force
# an arbitrarily large vector query and response (local DoS).
MAX_LIMIT = 100


def _clamp_limit(limit, default: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_LIMIT))


def _parse_iso(value) -> Optional[datetime]:
    """Parse an ISO 8601 string into a datetime, or return None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _offset_iso(base_value, offset_s) -> str:
    """Return base ISO time plus offset seconds, re-formatted as ISO 8601."""
    base = _parse_iso(base_value)
    if base is None:
        return ""
    try:
        offset = float(offset_s or 0)
    except (TypeError, ValueError):
        offset = 0.0
    return (base + timedelta(seconds=offset)).isoformat()


class QueryService:
    """Read-only retrieval over the store, vector index, and embedder."""

    def __init__(self, store, vectors, text_embedder):
        self.store = store
        self.vectors = vectors
        self.text_embedder = text_embedder
        # Private aliases for internal use.
        self._store = store
        self._vectors = vectors
        self._text_embedder = text_embedder

    # ------------------------------------------------------------------
    # People
    # ------------------------------------------------------------------

    def resolve_person(self, ref) -> tuple[Optional[dict], bool]:
        """Resolve a person reference to (person, ambiguous).

        An exact id match wins. Otherwise an exact case-insensitive name
        lookup runs: one match resolves, more than one is ambiguous, and
        zero matches resolves to None without guessing.
        """
        if not ref:
            return None, False

        try:
            by_id = self.store.get_person(ref)
        except Exception:
            by_id = None
        if by_id:
            return by_id, False

        matches = self.store.find_people_by_name(ref) or []
        if len(matches) == 1:
            return matches[0], False
        if len(matches) > 1:
            return None, True
        return None, False

    def list_people(self) -> list:
        """Return every enrolled person. People rows are safe as-is."""
        people = self.store.list_people() or []
        return [dict(person) if isinstance(person, dict) else person for person in people]

    # ------------------------------------------------------------------
    # Hit construction
    # ------------------------------------------------------------------

    def _speaker_name(self, seg: dict) -> str:
        person_id = seg.get("speaker_person_id")
        if person_id:
            try:
                person = self.store.get_person(person_id)
            except Exception:
                person = None
            if person and person.get("name"):
                return person["name"]
        return seg.get("speaker_label") or "Unknown"

    def _hit_for_segment(self, seg: dict, session, score) -> QueryHit:
        """Build a QueryHit from a stored segment and its session row."""
        if session is None:
            session_id = seg.get("session_id")
            if session_id:
                try:
                    session = self.store.get_session(session_id)
                except Exception:
                    session = None
        started_at = session.get("started_at") if isinstance(session, dict) else None
        return QueryHit(
            segment_id=seg.get("id"),
            session_id=seg.get("session_id"),
            speaker_name=self._speaker_name(seg),
            speaker_person_id=seg.get("speaker_person_id"),
            text=seg.get("text") or "",
            start_iso=_offset_iso(started_at, seg.get("start_offset_s")),
            end_iso=_offset_iso(started_at, seg.get("end_offset_s")),
            score=score,
        )

    # ------------------------------------------------------------------
    # Shared empty-result handling
    # ------------------------------------------------------------------

    def _empty(self, reason: str, candidates=None) -> QueryResult:
        return QueryResult(
            status=STATUS_EMPTY,
            reason=reason,
            results=[],
            candidates=list(candidates or []),
        )

    def _resolve_filter(self, person_ref):
        """Return (person, error_result). Exactly one of them is set when a
        person_ref was supplied; both are None when it was not."""
        if not person_ref:
            return None, None
        person, ambiguous = self.resolve_person(person_ref)
        if ambiguous:
            # Report the candidates, never pick one: the caller must
            # re-ask with a person id (BUILD_PLAN Section 2.6).
            matches = self.store.find_people_by_name(person_ref) or []
            candidates = [
                {"id": match.get("id"), "name": match.get("name")}
                for match in matches
                if isinstance(match, dict)
            ]
            return None, self._empty(REASON_AMBIGUOUS_NAME, candidates)
        if person is None:
            return None, self._empty(REASON_PERSON_NOT_FOUND)
        return person, None

    def _no_results_reason(self, person) -> str:
        """Reason for a search that kept zero hits.

        If a filtered person has segments but none of them are VERIFIED,
        that is reported distinctly from a plain no-match, so a caller can
        tell "nothing was ever verified for them" apart from "verified
        material exists, it just did not match".
        """
        if person is not None:
            segments = self.store.segments_for_person(person.get("id")) or []
            if segments and not any(
                seg.get("gate_status") == GATE_VERIFIED for seg in segments
            ):
                return REASON_NO_VERIFIED
        return REASON_NO_MATCHES

    def _keep_hit(self, seg, score, kept: list) -> None:
        session = None
        session_id = seg.get("session_id")
        if session_id:
            try:
                session = self.store.get_session(session_id)
            except Exception:
                session = None
        kept.append(self._hit_for_segment(seg, session, score))

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_conversations(self, query, person_ref=None, limit=10) -> QueryResult:
        """Semantic search over VERIFIED transcript chunks only."""
        limit = _clamp_limit(limit, 10)
        if self.store.people_count() == 0:
            return self._empty(REASON_NO_PEOPLE)

        person, error = self._resolve_filter(person_ref)
        if error is not None:
            return error
        person_id = person.get("id") if person else None

        embedding = self.text_embedder.embed(query)
        hits = self.vectors.query(COLLECTION_CHUNKS, embedding, n=limit * 3) or []

        kept: list[QueryHit] = []
        for hit in hits:
            if len(kept) >= limit:
                break
            metadata = getattr(hit, "metadata", None) or {}
            segment_id = metadata.get("segment_id") or getattr(hit, "id", None)
            if not segment_id:
                continue
            seg = self.store.get_segment(segment_id)
            if not seg:
                continue
            if seg.get("gate_status") != GATE_VERIFIED:
                continue
            if person_id is not None and seg.get("speaker_person_id") != person_id:
                continue
            self._keep_hit(seg, getattr(hit, "score", None), kept)

        if not kept:
            return self._empty(self._no_results_reason(person))
        return QueryResult(status=STATUS_OK, reason=None, results=kept)

    def exact_search(self, term, person_ref=None, limit=20) -> QueryResult:
        """Backup exact text search over VERIFIED segments only."""
        limit = _clamp_limit(limit, 20)
        if self.store.people_count() == 0:
            return self._empty(REASON_NO_PEOPLE)

        person, error = self._resolve_filter(person_ref)
        if error is not None:
            return error
        person_id = person.get("id") if person else None

        segments = self.store.search_segments_exact(term, limit) or []

        kept: list[QueryHit] = []
        for seg in segments:
            if len(kept) >= limit:
                break
            if not seg:
                continue
            if seg.get("gate_status") != GATE_VERIFIED:
                continue
            if person_id is not None and seg.get("speaker_person_id") != person_id:
                continue
            self._keep_hit(seg, None, kept)

        if not kept:
            return self._empty(self._no_results_reason(person))
        return QueryResult(status=STATUS_OK, reason=None, results=kept)

    # ------------------------------------------------------------------
    # Detail views
    # ------------------------------------------------------------------

    @staticmethod
    def _public_segment(seg: dict) -> dict:
        """Segment dict with raw_text stripped. Raw text is server only."""
        return {key: value for key, value in seg.items() if key != "raw_text"}

    def get_interaction(self, session_id) -> Optional[dict]:
        """Return one session plus its public segments, or None."""
        session = self.store.get_session(session_id)
        if not session:
            return None
        segments = self.store.list_segments(session_id) or []
        ordered = sorted(
            (seg for seg in segments if seg),
            key=lambda seg: (
                seg.get("seq") if seg.get("seq") is not None else 0,
                seg.get("start_offset_s") if seg.get("start_offset_s") is not None else 0,
            ),
        )
        result = dict(session)
        result["segments"] = [self._public_segment(seg) for seg in ordered]
        return result

    def get_person_profile(self, person_ref) -> Optional[dict]:
        """Return a person plus aggregate stats, or None if unresolved."""
        person, ambiguous = self.resolve_person(person_ref)
        if ambiguous or person is None:
            return None

        person_id = person.get("id")
        segments = self.store.segments_for_person(person_id) or []
        verified = [
            seg for seg in segments if seg and seg.get("gate_status") == GATE_VERIFIED
        ]

        sessions_count = len(
            {seg.get("session_id") for seg in segments if seg and seg.get("session_id")}
        )
        talk_seconds = round(
            sum(
                float(seg.get("end_offset_s") or 0) - float(seg.get("start_offset_s") or 0)
                for seg in verified
            ),
            1,
        )

        recent_hits: list[QueryHit] = []
        for seg in verified:
            session = None
            session_id = seg.get("session_id")
            if session_id:
                try:
                    session = self.store.get_session(session_id)
                except Exception:
                    session = None
            recent_hits.append(self._hit_for_segment(seg, session, None))
        # Most recent first: start_iso sorts chronologically for ISO times.
        recent_hits.sort(key=lambda hit: hit.start_iso or "", reverse=True)
        recent_segments = [asdict(hit) for hit in recent_hits[:5]]

        profile = dict(person)
        profile["sessions_count"] = sessions_count
        profile["verified_segments"] = len(verified)
        profile["talk_seconds"] = talk_seconds
        profile["recent_segments"] = recent_segments
        return profile
