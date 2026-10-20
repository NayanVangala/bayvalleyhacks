"""Split cleaned text into short reading friendly chunks.

Pure stdlib. Sentences are packed greedily, preferring breaks at
commas and semicolons, and never exceeding the char or word limits
unless a single word is longer than the limit on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]+|[^.!?]+$")
_PHRASE_RE = re.compile(r"[^,;]+[,;]?\s*")


@dataclass
class Chunk:
    index: int
    text: str
    word_count: int


def _split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for match in _SENTENCE_RE.finditer(paragraph):
            sentence = match.group(0).strip()
            if sentence:
                sentences.append(sentence)
    return sentences


def _split_phrases(sentence: str) -> list[str]:
    phrases: list[str] = []
    for match in _PHRASE_RE.finditer(sentence):
        phrase = match.group(0).strip()
        if phrase:
            phrases.append(phrase)
    return phrases or [sentence]


def _fits(words: list[str], extra_text: str, max_chars: int, max_words: int) -> bool:
    extra_words = extra_text.split()
    if len(words) + len(extra_words) > max_words:
        return False
    candidate = " ".join(words + extra_words) if words else extra_text
    return len(candidate) <= max_chars


def chunk_text(
    text: str, max_chars: int = 90, max_words: int = 12
) -> list[Chunk]:
    """Split text into chunks bounded by max_chars and max_words."""
    if not text or not text.strip():
        return []
    if max_chars <= 0:
        max_chars = 90
    if max_words <= 0:
        max_words = 12

    raw_chunks: list[str] = []

    for sentence in _split_sentences(text):
        phrases = _split_phrases(sentence)
        current: list[str] = []

        def flush() -> None:
            if current:
                raw_chunks.append(" ".join(current).strip())
                current.clear()

        for phrase in phrases:
            phrase_words = phrase.split()
            # Phrase fits in the current chunk: pack it greedily.
            if current and _fits(current, phrase, max_chars, max_words):
                current.extend(phrase_words)
                continue
            # Phrase does not fit alongside current: break here.
            # This is a comma or semicolon boundary when one exists.
            if current:
                flush()
            # Phrase fits on its own: start a new chunk with it.
            if _fits([], phrase, max_chars, max_words):
                current.extend(phrase_words)
                continue
            # Phrase alone is too long: pack its words greedily.
            for word in phrase_words:
                if current and not _fits(current, word, max_chars, max_words):
                    flush()
                current.append(word)
        flush()

    chunks: list[Chunk] = []
    for idx, chunk_str in enumerate(raw_chunks):
        cleaned = " ".join(chunk_str.split()).strip()
        if not cleaned:
            continue
        chunks.append(
            Chunk(index=len(chunks), text=cleaned, word_count=len(cleaned.split()))
        )
    # Reassign indexes in case any empty chunk was skipped.
    for idx, chunk in enumerate(chunks):
        chunk.index = idx
    return chunks
