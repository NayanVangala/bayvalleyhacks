"""Slack digest builder for OnRecord (BUILD_PLAN Section 2.7).

Slack is a sink, never a dependency; a digest with a failed recap still
posts the computed template with the unverified marker.

The digest text is built from a fixed template whose slots are computed
only from stored segment data. Only VERIFIED segments feed the judged
slots (participants, talk time, topics, action items). REVIEW and
SILENCE segments appear only in the counts line and the open items line.

The one free text slot, the recap, is gated by a deterministic citation
checker: validate_recap must return no errors before the recap text is
allowed into the digest. An invalid recap is withheld, never silently
posted, and the digest status becomes DRAFT instead of POSTABLE.
"""

from __future__ import annotations

import re
import string
from collections import Counter

from .interfaces import GATE_REVIEW, GATE_SILENCE, GATE_VERIFIED

STOPWORDS_V1 = frozenset(
    {
        "a",
        "about",
        "after",
        "all",
        "also",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "but",
        "by",
        "can",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "here",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "me",
        "more",
        "most",
        "my",
        "no",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "our",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
        "you",
        "your",
    }
)

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_CITATION_RE = re.compile(r"\[seg:([^\]]+)\]")
_CITATION_AT_END_RE = re.compile(r"\[seg:[^\]]+\]\s*$")
_QUOTED_RE = re.compile(r'"([^"]+)"')
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _segment_text(segment):
    return _get(segment, "text", "") or ""


def _split_sentences(text):
    """Split text on sentence punctuation, keeping trailing citations.

    A citation that follows the sentence punctuation belongs to the
    sentence before it, so the scan extends past the punctuation when
    a [seg:...] token follows it directly. A period between two digits
    is a decimal point, not a sentence boundary.
    """
    sentences = []
    n = len(text)
    start = 0
    i = 0
    while i < n:
        ch = text[i]
        if ch in ".!?":
            if (
                ch == "."
                and i > start
                and text[i - 1].isdigit()
                and i + 1 < n
                and text[i + 1].isdigit()
            ):
                i += 1
                continue
            end = i + 1
            probe = end
            while probe < n and text[probe] in " \t":
                probe += 1
            if text.startswith("[seg:", probe):
                close = text.find("]", probe)
                if close != -1:
                    end = close + 1
            sentence = text[start:end].strip()
            if sentence:
                sentences.append(sentence)
            start = end
            i = end
        else:
            i += 1
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def validate_recap(recap_text, verified_segments) -> list:
    """Check a recap against the verified segments it claims to cite.

    Returns a list of human readable error strings. An empty list
    means the recap is valid and may be posted.
    """
    errors = []
    if recap_text is None:
        return ["Recap text is missing"]
    segments = list(verified_segments or [])
    by_id = {}
    for segment in segments:
        seg_id = _get(segment, "id")
        if seg_id is not None:
            by_id[str(seg_id)] = segment

    # Rule 4: every double quoted substring must appear verbatim in
    # some verified segment text.
    all_texts = [_segment_text(segment) for segment in segments]
    for quoted in _QUOTED_RE.findall(recap_text):
        if not any(quoted in text for text in all_texts):
            errors.append(
                'Quoted text "'
                + quoted
                + '" does not appear verbatim in any verified segment'
            )

    sentences = _split_sentences(recap_text)
    for sentence in sentences:
        if not sentence.strip():
            continue
        cited_ids = _CITATION_RE.findall(sentence)
        # Rule 1: every sentence must end with a citation. The citation
        # may sit right before the final punctuation or at the very end.
        core = sentence.strip().rstrip(".!?").strip()
        if not cited_ids:
            errors.append(
                "Sentence has no citation [seg:<segment-id>]: " + sentence.strip()
            )
            continue
        if not _CITATION_AT_END_RE.search(core):
            errors.append(
                "Sentence must end with a citation [seg:<segment-id>]: "
                + sentence.strip()
            )
        # Rule 2: every cited id must be a verified segment id.
        valid_cited = []
        for cited_id in cited_ids:
            if cited_id not in by_id:
                errors.append(
                    "Cited segment id is not a verified segment: " + cited_id
                )
            else:
                valid_cited.append(cited_id)
        # Rule 3: every number token in the sentence must appear among
        # the number tokens of the segments cited by that sentence.
        # Citation tokens themselves are not recap numbers.
        cleaned = _CITATION_RE.sub("", sentence)
        recap_numbers = _NUMBER_RE.findall(cleaned)
        if recap_numbers:
            cited_numbers = set()
            for cited_id in valid_cited:
                cited_numbers.update(
                    _NUMBER_RE.findall(_segment_text(by_id[cited_id]))
                )
            for number in recap_numbers:
                if number not in cited_numbers:
                    errors.append(
                        "Number "
                        + number
                        + " in sentence does not appear in its cited segments: "
                        + sentence.strip()
                    )
    return errors


def _sorted_segments(segments):
    def sort_key(item):
        seq = _get(item, "seq")
        if isinstance(seq, (int, float)):
            return (0, seq)
        return (1, 0)

    return sorted(segments, key=sort_key)


def _format_duration(total_seconds) -> str:
    total = int(round(float(total_seconds or 0)))
    if total < 0:
        total = 0
    minutes = total // 60
    seconds = total % 60
    return str(minutes) + ":" + format(seconds, "02d")


def build_digest(store, session_id, recap_text=None) -> dict:
    """Build a digest dict {text, status, errors} for one session.

    Slots are computed only from data returned by the store. Only
    VERIFIED segments feed participants, talk time, topics and action
    items. A recap that fails validation is withheld from the text,
    the errors are returned, and the status is DRAFT.
    """
    session = None
    if store is not None:
        try:
            session = store.get_session(session_id)
        except Exception:
            session = None
    raw_segments = []
    if store is not None:
        raw_segments = store.list_segments(session_id) or []
    segments = _sorted_segments(list(raw_segments))

    verified = [
        s for s in segments if _get(s, "gate_status") == GATE_VERIFIED
    ]
    review = [s for s in segments if _get(s, "gate_status") == GATE_REVIEW]
    silence = [s for s in segments if _get(s, "gate_status") == GATE_SILENCE]

    title = _get(session, "title") or _get(session, "id") or session_id
    started_at = _get(session, "started_at")
    ended_at = _get(session, "ended_at")

    lines = []
    lines.append("OnRecord digest: " + str(title))
    if started_at and ended_at:
        lines.append("Time: " + str(started_at) + " to " + str(ended_at))
    elif started_at:
        lines.append("Time: " + str(started_at) + " to (not ended)")
    elif ended_at:
        lines.append("Time: (unknown) to " + str(ended_at))
    else:
        lines.append("Time: unknown")

    # Participants: distinct speaker labels over VERIFIED segments.
    participant_counts = {}
    participant_order = []
    for segment in verified:
        label = _get(segment, "speaker_label") or "Unknown"
        if label not in participant_counts:
            participant_counts[label] = 0
            participant_order.append(label)
        participant_counts[label] += 1
    if participant_order:
        parts = ", ".join(
            label + " (" + str(participant_counts[label]) + ")"
            for label in participant_order
        )
        lines.append("Participants: " + parts)
    else:
        lines.append("Participants: none")

    lines.append(
        "Counts: VERIFIED "
        + str(len(verified))
        + ", REVIEW "
        + str(len(review))
        + ", SILENCE "
        + str(len(silence))
    )

    # Talk time per speaker over VERIFIED segments.
    talk_seconds = {}
    talk_order = []
    for segment in verified:
        label = _get(segment, "speaker_label") or "Unknown"
        if label not in talk_seconds:
            talk_seconds[label] = 0.0
            talk_order.append(label)
        start = _get(segment, "start_offset_s", 0) or 0
        end = _get(segment, "end_offset_s", 0) or 0
        try:
            duration = float(end) - float(start)
        except (TypeError, ValueError):
            duration = 0.0
        if duration > 0:
            talk_seconds[label] += duration
    if talk_order:
        talk_parts = ", ".join(
            label + ": " + _format_duration(talk_seconds[label])
            for label in talk_order
        )
        lines.append("Talk time: " + talk_parts)
    else:
        lines.append("Talk time: none")

    # Topics: top 8 exact tokens over VERIFIED texts.
    counts = Counter()
    first_seen = {}
    stream_index = 0
    for segment in verified:
        text = _segment_text(segment).lower()
        for token in _TOKEN_RE.findall(text):
            token = token.strip(string.punctuation)
            if len(token) < 3 or token in STOPWORDS_V1:
                continue
            if token not in first_seen:
                first_seen[token] = stream_index
            stream_index += 1
            counts[token] += 1
    ranked = sorted(
        counts.items(), key=lambda item: (-item[1], first_seen[item[0]])
    )
    topics = [token for token, _count in ranked[:8]]
    if topics:
        lines.append("Topics: " + ", ".join(topics))
    else:
        lines.append("Topics: none")

    # Action items: VERIFIED segments starting with ACTION:, verbatim.
    action_segments = [
        s
        for s in verified
        if _segment_text(s).lstrip().startswith("ACTION:")
    ]
    if action_segments:
        lines.append("Action items:")
        for segment in action_segments:
            seg_id = _get(segment, "id")
            lines.append(
                '- "'
                + _segment_text(segment)
                + '" [seg:'
                + str(seg_id)
                + "]"
            )
    else:
        lines.append("Action items: none flagged")

    # Open items: REVIEW segments awaiting review.
    if review:
        lines.append(
            "Open items: "
            + str(len(review))
            + " segment(s) awaiting review"
        )
    else:
        lines.append("Open items: none")

    errors = []
    status = "POSTABLE"
    if recap_text is not None:
        errors = validate_recap(recap_text, verified)
        if errors:
            status = "DRAFT"
            lines.append("[UNVERIFIED recap withheld]")
        else:
            lines.append("Recap: " + recap_text)

    text = "\n".join(lines)
    return {"text": text, "status": status, "errors": errors}
