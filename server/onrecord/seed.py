"""Rehearsal fixture loader for OnRecord (Phase 7).

Quarantine rule: this module seeds fake demo data, so it must never run
against a live or judging setup by accident. main() refuses to run unless
the settings say rehearsal mode is on (env ONRECORD_REHEARSAL=1) or the
caller passes --force on the command line. On refusal it prints a clear
message to stderr and exits with code 2.

When allowed it:
- enrolls two people, Maya Chen and Liam Park, through EnrollmentService
  so face and voice prints exist for them,
- creates one rehearsal session from the phone source,
- inserts 8 verified segments of a budget planning conversation,
- attributes each segment to its speaker with source MANUAL, because the
  seed is labeled by hand and should say so honestly,
- upserts each transcript chunk into the transcript chunks collection so
  semantic search can find it, for example the demo query
  "what did Maya say about the budget",
- ends the session and prints a JSON summary.

Run it with:
    ONRECORD_REHEARSAL=1 python3 -m onrecord.seed
"""

from __future__ import annotations

import json
import math
import struct
import sys

from .interfaces import ATTR_MANUAL, COLLECTION_CHUNKS, GATE_VERIFIED

SESSION_TITLE = "Rehearsal: budget planning chat"
SESSION_SOURCE = "phone"
SEGMENT_SECONDS = 4.0
DEFAULT_SAMPLE_RATE = 16000

# (name, photo bytes, voice sine frequency in Hz)
SEED_PEOPLE = [
    ("Maya Chen", b"seed-photo-maya-chen", 220),
    ("Liam Park", b"seed-photo-liam-park", 330),
]

# (speaker name, line, avg_logprob). Speakers alternate, starting with Maya.
# avg_logprob values stay around -0.2 so every line passes the VERIFIED gate.
SEED_SEGMENTS = [
    (
        "Maya Chen",
        "Let us lock the budget for the event. The venue budget is 2400 dollars total, and we cannot go over that.",
        -0.19,
    ),
    (
        "Liam Park",
        "Got it. How much of that budget should we plan for catering, and is food counted inside the 2400 dollars?",
        -0.21,
    ),
    (
        "Maya Chen",
        "Catering is capped at 900 dollars, and the rest of the money goes to prizes.",
        -0.18,
    ),
    (
        "Liam Park",
        "Okay, confirmed. Catering stays under 900 dollars and we track every receipt against the budget.",
        -0.22,
    ),
    (
        "Maya Chen",
        "Please send me the final budget breakdown by Friday. That deadline gives us time to fix anything before the event.",
        -0.20,
    ),
    (
        "Liam Park",
        "I will draft the budget sheet tonight and share it so you can check the venue and catering numbers.",
        -0.19,
    ),
    (
        "Maya Chen",
        "Thanks. If catering comes in under 900 dollars, we can move the leftover money into the prize pool.",
        -0.21,
    ),
    (
        "Liam Park",
        "Sounds good. I will confirm the catering order on Friday once you approve the final budget.",
        -0.18,
    ),
]


def _sine_pcm(freq_hz: int, sample_rate: int, duration_s: float = 1.0) -> bytes:
    """Return PCM16 mono sine wave bytes, generated with math and struct."""
    n_samples = int(sample_rate * duration_s)
    amplitude = 12000
    samples = []
    for i in range(n_samples):
        value = int(amplitude * math.sin(2.0 * math.pi * freq_hz * i / sample_rate))
        samples.append(value)
    return struct.pack("<%dh" % n_samples, *samples)


def _extract_id(result, *keys):
    """Pull an id out of whatever a sibling create or enroll call returns.

    Accepts a plain scalar id, a dict with one of the given keys (or "id",
    or a nested "person", "session", or "segment" dict), or an object with
    a matching attribute. Returns the scalar id unchanged when possible so
    int and string ids both keep working with the store.
    """
    if result is None:
        return None
    if isinstance(result, (str, int)):
        return result
    if isinstance(result, dict):
        for key in keys + ("id",):
            value = result.get(key)
            if isinstance(value, (str, int)):
                return value
        for nested_key in ("person", "session", "segment"):
            nested = result.get(nested_key)
            if nested is not None:
                found = _extract_id(nested, *keys)
                if found is not None:
                    return found
        return None
    for key in keys + ("id",):
        value = getattr(result, key, None)
        if isinstance(value, (str, int)):
            return value
    return result


def _load_class(module_names, class_name):
    """Import class_name from the first module in module_names that has it."""
    last_error = None
    for module_name in module_names:
        try:
            module = __import__(
                "onrecord." + module_name, fromlist=[class_name]
            )
        except ImportError as exc:
            last_error = exc
            continue
        cls = getattr(module, class_name, None)
        if cls is not None:
            return cls
    raise ImportError(
        "could not import %s from any of: %s" % (class_name, ", ".join(module_names))
    ) from last_error


def _build_vector_store(settings):
    """Prefer the configured vector store, fall back to the fake.

    The rehearsal seed must run offline, so if the real store (Chroma) is
    not importable or not configured yet, FakeVectorStore keeps the seed
    usable for smoke runs.
    """
    try:
        from .chroma_store import get_vector_store

        return get_vector_store(settings)
    except Exception:
        pass
    fake_cls = _load_class(
        ("chroma_store", "vector_store", "vectors"), "FakeVectorStore"
    )
    try:
        return fake_cls()
    except TypeError:
        return fake_cls(settings)


def seed(settings) -> dict:
    """Load the rehearsal fixtures and return the summary dict."""
    from .config import get_settings  # noqa: F401  (kept for parity with specs)
    from .db import Store
    from .identity import EnrollmentService

    text_embedder_cls = _load_class(("embeddings",), "HashingTextEmbedder")
    face_embedder_cls = _load_class(
        ("face", "embeddings", "biometrics"), "FakeFaceEmbedder"
    )
    voice_embedder_cls = _load_class(
        ("voice", "embeddings", "biometrics"), "FakeVoiceEmbedder"
    )

    sample_rate = getattr(settings, "sample_rate", None) or DEFAULT_SAMPLE_RATE

    store = Store(settings)
    vectors = _build_vector_store(settings)
    text_embedder = text_embedder_cls()
    face_embedder = face_embedder_cls()
    voice_embedder = voice_embedder_cls()

    enrollment = EnrollmentService(store, vectors, face_embedder, voice_embedder)

    people_ids = {}
    people_order = []
    for name, photo_bytes, freq_hz in SEED_PEOPLE:
        voice_pcm = _sine_pcm(freq_hz, sample_rate, duration_s=1.0)
        enrolled = enrollment.enroll_person(
            name, photo_bytes, voice_pcm, "Rehearsal seed fixture."
        )
        person_id = _extract_id(enrolled, "person_id")
        if person_id is None:
            raise RuntimeError("enroll_person returned no person id for %r" % name)
        people_ids[name] = person_id
        people_order.append(person_id)

    session_result = store.create_session(SESSION_TITLE, SESSION_SOURCE, True)
    session_id = _extract_id(session_result, "session_id")
    if session_id is None:
        raise RuntimeError("create_session returned no session id")

    for seq, (speaker_name, text, avg_logprob) in enumerate(SEED_SEGMENTS):
        start_offset_s = seq * SEGMENT_SECONDS
        end_offset_s = start_offset_s + SEGMENT_SECONDS
        segment_result = store.add_segment(
            session_id,
            seq,
            text,
            text,
            avg_logprob,
            0.01,
            GATE_VERIFIED,
            0,
            start_offset_s,
            end_offset_s,
        )
        segment_id = _extract_id(segment_result, "segment_id")
        if segment_id is None:
            raise RuntimeError("add_segment returned no segment id for seq %d" % seq)

        person_id = people_ids[speaker_name]
        store.set_segment_speaker(
            segment_id,
            person_id,
            speaker_name,
            ATTR_MANUAL,
            None,
            None,
        )

        metadata = {
            "segment_id": segment_id,
            "session_id": session_id,
            "person_id": person_id,
            "speaker_name": speaker_name,
            "gate_status": GATE_VERIFIED,
        }
        vectors.upsert(
            COLLECTION_CHUNKS,
            str(segment_id),
            text_embedder.embed(text),
            metadata,
            text,
        )

    store.end_session(session_id)

    return {
        "people": people_order,
        "session_id": session_id,
        "segments": len(SEED_SEGMENTS),
    }


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    else:
        argv = list(argv)

    from .config import get_settings

    settings = get_settings()

    forced = "--force" in argv
    if not getattr(settings, "rehearsal", False) and not forced:
        print(
            "seed.py: refusing to run. Rehearsal fixtures are quarantined "
            "and may only load in rehearsal mode. Set ONRECORD_REHEARSAL=1 "
            "or pass --force to load them.",
            file=sys.stderr,
        )
        sys.exit(2)

    summary = seed(settings)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
