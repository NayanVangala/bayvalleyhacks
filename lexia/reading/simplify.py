"""Text simplification for Lexia Read Mode.

Pure stdlib, no LLM and no network. The rule based simplifier swaps
hard words for simpler ones and splits very long sentences.
"""

from __future__ import annotations

import os
import re
from typing import Protocol, runtime_checkable

MAX_SENTENCE_WORDS = 22

HARD_WORDS: dict[str, str] = {
    "utilize": "use",
    "approximately": "about",
    "purchase": "buy",
    "assistance": "help",
    "difficult": "hard",
    "commence": "start",
    "sufficient": "enough",
    "numerous": "many",
    "require": "need",
    "attempt": "try",
    "obtain": "get",
    "frequently": "often",
    "therefore": "so",
    "additional": "extra",
    "currently": "now",
    "demonstrate": "show",
    "request": "ask",
    "provide": "give",
    "necessary": "needed",
    "location": "place",
    "method": "way",
    "objective": "goal",
    "previous": "earlier",
    "respond": "answer",
    "select": "pick",
    "transform": "change",
    "information": "facts",
}

_HARD_WORD_RE = re.compile(
    r"\b(" + "|".join(sorted(HARD_WORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]+|[^.!?]+$")
_CLAUSE_SPLIT_RE = re.compile(
    r",\s+|;\s+|\s+and\s+|\s+but\s+|\s+or\s+|\s+because\s+|\s+which\s+"
)


def _replace_match(match: re.Match[str]) -> str:
    original = match.group(0)
    replacement = HARD_WORDS[original.lower()]
    if original[:1].isupper():
        replacement = replacement[:1].upper() + replacement[1:]
    return replacement


def _replace_hard_words(text: str) -> str:
    return _HARD_WORD_RE.sub(_replace_match, text)


def _finish_sentence(text: str, ending: str) -> str:
    cleaned = text.strip().strip(",;").strip()
    if not cleaned:
        return ""
    cleaned = cleaned[:1].upper() + cleaned[1:]
    if cleaned[-1:] in (".", "!", "?"):
        return cleaned
    return cleaned + ending


def _split_long_sentence(sentence: str) -> list[str]:
    words = sentence.split()
    if len(words) <= MAX_SENTENCE_WORDS:
        return [sentence]

    ending = "."
    core = sentence.strip()
    if core and core[-1] in (".", "!", "?"):
        ending = core[-1]
        core = core[:-1]

    clauses = [c.strip() for c in _CLAUSE_SPLIT_RE.split(core) if c.strip()]

    if len(clauses) <= 1:
        # No clause boundary: split by word count into shorter pieces.
        pieces: list[str] = []
        for start in range(0, len(words), MAX_SENTENCE_WORDS):
            piece_words = words[start : start + MAX_SENTENCE_WORDS]
            piece = " ".join(piece_words)
            # Strip a trailing sentence mark from non-final pieces.
            is_last = start + MAX_SENTENCE_WORDS >= len(words)
            piece_ending = ending if is_last else "."
            finished = _finish_sentence(piece.rstrip(".!?"), piece_ending)
            if finished:
                pieces.append(finished)
        return pieces or [sentence]

    pieces = []
    current: list[str] = []
    current_words = 0
    for clause in clauses:
        clause_words = len(clause.split())
        if clause_words > MAX_SENTENCE_WORDS:
            if current:
                finished = _finish_sentence(", ".join(current), ".")
                if finished:
                    pieces.append(finished)
                current = []
                current_words = 0
            clause_word_list = clause.split()
            for start in range(0, len(clause_word_list), MAX_SENTENCE_WORDS):
                part = " ".join(
                    clause_word_list[start : start + MAX_SENTENCE_WORDS]
                )
                finished = _finish_sentence(part, ".")
                if finished:
                    pieces.append(finished)
            continue
        if current and current_words + clause_words > MAX_SENTENCE_WORDS:
            finished = _finish_sentence(", ".join(current), ".")
            if finished:
                pieces.append(finished)
            current = [clause]
            current_words = clause_words
        else:
            current.append(clause)
            current_words += clause_words
    if current:
        finished = _finish_sentence(", ".join(current), ending)
        if finished:
            pieces.append(finished)
    return pieces or [sentence]


@runtime_checkable
class Simplifier(Protocol):
    def simplify(self, text: str) -> str:
        ...


class IdentitySimplifier:
    """Simplifier that returns text unchanged."""

    def simplify(self, text: str) -> str:
        return text


class RuleBasedSimplifier:
    """Simplifier based on word swaps and sentence splitting."""

    def simplify(self, text: str) -> str:
        if not text or not text.strip():
            return text
        replaced = _replace_hard_words(text)
        paragraphs: list[str] = []
        for paragraph in replaced.split("\n"):
            if not paragraph.strip():
                paragraphs.append(paragraph)
                continue
            out_sentences: list[str] = []
            for match in _SENTENCE_RE.finditer(paragraph):
                sentence = match.group(0).strip()
                if not sentence:
                    continue
                if len(sentence.split()) > MAX_SENTENCE_WORDS:
                    out_sentences.extend(_split_long_sentence(sentence))
                else:
                    out_sentences.append(sentence)
            paragraphs.append(" ".join(out_sentences))
        return "\n".join(paragraphs)


def get_simplifier() -> Simplifier:
    """Return the simplifier selected by the LEXIA_SIMPLIFY env var."""
    if os.environ.get("LEXIA_SIMPLIFY", "").strip().lower() == "rule":
        return RuleBasedSimplifier()
    return IdentitySimplifier()
