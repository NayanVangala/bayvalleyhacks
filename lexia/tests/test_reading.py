"""Contract tests for lexia.reading.

Contracts covered:
* clean_text joins hyphenated line breaks ("exam-\\nple" becomes
  "example"), collapses runs of whitespace, and drops junk lines.
* chunk_text respects both the max_chars and max_words limits, chunk
  indexes run sequentially from 0, and chunk_text("") == [].
* RuleBasedSimplifier replaces "utilize" with "use" and splits a
  sentence of more than 22 words into more than one sentence.
* SessionStore supports a create/get roundtrip, returns None for an
  unknown id, and accepts set_audio for a stored session.
"""

import pytest

from lexia.reading import (
    RuleBasedSimplifier,
    SessionStore,
    chunk_text,
    clean_text,
)

INDEX_KEYS = ("index", "chunk_index", "idx", "position", "order")
AUDIO_KEYS = ("audio_url", "audio_path", "audio", "audio_file", "tts_path")


def chunk_text_value(chunk):
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, dict):
        return chunk["text"]
    return chunk.text


def chunk_index_value(chunk, fallback):
    if isinstance(chunk, dict):
        for key in INDEX_KEYS:
            if key in chunk:
                return chunk[key]
        return fallback
    for key in INDEX_KEYS:
        if hasattr(chunk, key):
            return getattr(chunk, key)
    return fallback


def simplify(simplifier, text):
    for method_name in ("simplify", "simplify_text", "process", "run"):
        method = getattr(simplifier, method_name, None)
        if callable(method):
            result = method(text)
            return result if isinstance(result, str) else str(result)
    if callable(simplifier):
        result = simplifier(text)
        return result if isinstance(result, str) else str(result)
    raise AssertionError("RuleBasedSimplifier exposes no simplify method")


def split_sentences(text):
    parts = [part.strip() for part in text.replace("!", ".").replace("?", ".").split(".")]
    return [part for part in parts if part]


# clean_text


def test_clean_text_joins_hyphenated_line_break():
    cleaned = clean_text("This is an exam-\nple of wrapped text.")
    assert "example" in cleaned
    assert "exam-" not in cleaned
    assert "exam- ple" not in cleaned


def test_clean_text_collapses_whitespace():
    cleaned = clean_text("Hello    world.\n\n\nNext\t line   here.")
    assert "\t" not in cleaned
    assert "   " not in cleaned
    assert "\n\n\n" not in cleaned
    assert "Hello world." in cleaned
    assert "Next line here." in cleaned


def test_clean_text_drops_junk_lines():
    raw = "A real sentence stays here.\n||\nAnother real sentence stays."
    cleaned = clean_text(raw)
    assert "A real sentence stays here." in cleaned
    assert "Another real sentence stays." in cleaned
    assert "||" not in cleaned


def test_clean_text_handles_empty_input():
    assert clean_text("") == ""
    assert clean_text("   \n  \n") == ""


# chunk_text


def test_chunk_text_empty_returns_empty_list():
    assert chunk_text("") == []


def test_chunk_text_respects_limits_and_indexes():
    words = ["word%02d" % i for i in range(60)]
    text = " ".join(words) + "."

    chunks = chunk_text(text, max_chars=90, max_words=12)

    assert chunks, "long text should produce at least one chunk"
    assert len(chunks) > 1, "60 words cannot fit in a single 12 word chunk"
    for position, chunk in enumerate(chunks):
        value = chunk_text_value(chunk)
        assert len(value) <= 90, "chunk exceeds max_chars: %r" % value
        assert len(value.split()) <= 12, "chunk exceeds max_words: %r" % value
        assert chunk_index_value(chunk, position) == position


def test_chunk_text_default_limits():
    text = " ".join("filler%d" % i for i in range(40)) + "."
    chunks = chunk_text(text)
    assert chunks
    for chunk in chunks:
        value = chunk_text_value(chunk)
        assert len(value) <= 90
        assert len(value.split()) <= 12


def test_chunk_text_preserves_all_words():
    text = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
    chunks = chunk_text(text, max_chars=90, max_words=12)
    rejoined = " ".join(chunk_text_value(chunk) for chunk in chunks)
    assert rejoined.split() == text.split()


# RuleBasedSimplifier


def test_simplifier_replaces_utilize_with_use():
    simplifier = RuleBasedSimplifier()
    output = simplify(simplifier, "They utilize the new method every day.")
    assert "utilize" not in output.lower()
    assert "use" in output.lower()


def test_simplifier_splits_long_sentence():
    long_sentence = (
        "The quick brown fox jumps over the lazy dog near the river bank, "
        "and then it runs through the green forest toward the tall "
        "mountains beyond the wide valley at dawn today."
    )
    assert len(long_sentence.split()) > 22

    simplifier = RuleBasedSimplifier()
    output = simplify(simplifier, long_sentence)

    assert len(split_sentences(output)) > len(split_sentences(long_sentence))
    for word in ("fox", "forest", "mountains"):
        assert word in output


# SessionStore


def extract_session_id(created):
    if isinstance(created, str):
        return created
    if isinstance(created, dict):
        for key in ("session_id", "id"):
            if key in created:
                return created[key]
    for key in ("session_id", "id"):
        if hasattr(created, key):
            return getattr(created, key)
    raise AssertionError("cannot find a session id on %r" % (created,))


def create_session(store):
    text = "The cat sat on the mat. It was a sunny day."
    chunks = chunk_text(text, max_chars=90, max_words=12)
    try:
        return store.create(
            raw_text=text,
            cleaned_text=text,
            chunks=chunks,
            simplified=False,
            engine="fake",
            mean_confidence=0.95,
        )
    except TypeError:
        return store.create(text, text, chunks, False, "fake", 0.95)


def session_field(session, *names):
    if session is None:
        return None
    if isinstance(session, dict):
        for name in names:
            if name in session:
                return session[name]
        return None
    for name in names:
        if hasattr(session, name):
            return getattr(session, name)
    return None


def test_session_store_create_get_roundtrip():
    store = SessionStore()
    created = create_session(store)
    session_id = extract_session_id(created)

    fetched = store.get(session_id)
    assert fetched is not None
    assert extract_session_id(fetched) == session_id

    stored_text = session_field(fetched, "text", "raw_text", "cleaned_text", "original_text")
    if stored_text is not None:
        assert "cat" in stored_text
    stored_chunks = session_field(fetched, "chunks")
    if stored_chunks is not None:
        assert len(stored_chunks) > 0


def test_session_store_unknown_id_returns_none():
    store = SessionStore()
    assert store.get("nope") is None


def test_session_store_set_audio():
    store = SessionStore()
    created = create_session(store)
    session_id = extract_session_id(created)

    audio_value = "/audio/test-session.wav"
    result = store.set_audio(session_id, audio_value)
    assert result is not False, "set_audio should accept a stored session id"

    fetched = store.get(session_id)
    assert fetched is not None
    for key in AUDIO_KEYS:
        value = session_field(fetched, key)
        if value:
            assert "test-session" in str(value)
            break
