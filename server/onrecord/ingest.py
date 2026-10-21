"""FastAPI server and WebSocket ingest for OnRecord (BUILD_PLAN Section 3).

Run it with:

    python -m onrecord.ingest

HTTP surface:

- GET  /healthz
- POST /sessions  {title?, source?}
- POST /sessions/{id}/arm | /start | /pause | /resume | /end
- GET  /sessions/{id}/status
- GET  /api/sessions
- GET  /api/sessions/{id}          (session + segments, raw_text stripped)
- GET  /api/people
- POST /api/enroll                 {name, photo_b64, voice_b64, notes?}
                                 photo_b64 = base64 JPEG; voice_b64 = base64
                                 raw PCM16 mono 16kHz little endian (NO WAV header)
                                 (alias: POST /api/people/enroll, same contract)
- POST /api/search                 {query, person?, limit?}
- POST /api/segments/{id}/correct  {person_id, operator_id?}
- POST /api/sessions/{id}/digest   {recap_text?, confirm?}

WebSocket ingest: WS /ingest/audio?session_id=<id>

The wire protocol is bridge-ios/PROTOCOL.md. Text frames are JSON
control messages. The bridge vocabulary is:

    {"type": "hello" | "session_start" | "talk_start" | "talk_end"
             | "session_pause" | "session_resume" | "session_end"
             | "ping" | "photo_meta" | "tag_moment"}

and the REST-era vocabulary is accepted on the same socket:

    {"type": "arm" | "start" | "talk_stop" | "new_segment" | "pause"
             | "resume" | "photo" | "end"}

Unknown control types are ignored, per PROTOCOL.md section 3; they
are never an error. "photo" (legacy) means the NEXT binary frame on
the socket is a JPEG photo. Photos are kept as the session's latest
photo and handed to the pipeline with every segment sealed
afterwards, so face identification can use it.

Binary frames have a 16-byte header. Two layouts share the socket:

- Bridge frames (PROTOCOL.md section 4): magic "OR", kind byte
  (1 = audio, 2 = photo), flags byte, then seg_idx, frame_seq and
  capture_ts_ms as big endian uint32s. Audio payloads are PCM16
  mono, exactly 3200 bytes per frame except a shorter final frame
  of a segment. Photo payloads are whole JPEGs; a photo frame is
  never routed into the audio path.
- Legacy frames (the pre-bridge client): struct format "<IIQ",
  seg_idx / frame_seq / capture_ts_ms little endian, no magic, no
  kind; the payload is always PCM16 audio.

A frame shorter than 16 bytes, a bridge frame with bad magic or an
unknown kind, and a truncated or oversize bridge audio payload are
dropped whole and reported with a non-fatal error frame; the socket
stays open. Duplicate frames (same session, seg_idx, frame_seq,
capture_ts_ms, length) are dropped: a reconnect replay must never
append the same audio twice. Timeline losses (pause gaps, frame
gaps, reconnects, source failover) are recorded as explicit gap
records on the session and returned in API payloads, never silently
stitched over.

Server to client messages on the ingest socket:

    {"type": "welcome", "session_id": ..., "server_time": ...,
     "ok": true}                                 reply to "hello"
    {"type": "pong", "t0_mono_ms": ..., "server_time": ...}
                                                   reply to "ping"
    {"type": "segments", "segments": [...], "gaps": [...]}
                                                   after each sealed batch
    {"type": "session_ended"}                 after an end control frame
    {"type": "error", "code": ..., "message": ..., "fatal": false}
                                              per-message failure, socket
                                              stays open unless fatal

Segment dicts never leave this server with their raw_text field attached.
raw_text is server-only (see BUILD_PLAN Section 3, key decision 4); the
strip helper below is applied to every payload that leaves the server.

All sibling modules (config, db, state_machine, pipeline, identity, ...)
are imported lazily inside create_app and the endpoint helpers, never at
module import time, so this module imports cleanly on its own.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import datetime
import importlib
import json
import struct
import time
from typing import Any, Optional

try:
    from fastapi import FastAPI, HTTPException, Request, WebSocket
    from fastapi import WebSocketDisconnect

    HAS_FASTAPI = True
except ImportError:  # pragma: no cover, exercised only without fastapi
    FastAPI = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment]
    Request = None  # type: ignore[assignment]
    WebSocket = None  # type: ignore[assignment]
    WebSocketDisconnect = Exception  # type: ignore[assignment,misc]
    HAS_FASTAPI = False

__all__ = [
    "HAS_FASTAPI",
    "AUDIO_HEADER",
    "BRIDGE_HEADER",
    "FrameError",
    "parse_binary_frame",
    "create_app",
    "main",
    "strip_raw_text",
]

# Legacy binary frame header: seg_idx (uint32), frame_seq (uint32),
# capture_ts_ms (uint64), all little endian. 16 bytes total. Kept for
# the pre-bridge client and its tests; new senders use the bridge
# header below (bridge-ios/PROTOCOL.md section 4).
AUDIO_HEADER = struct.Struct("<IIQ")

# Bridge binary frame header (PROTOCOL.md section 4): magic "OR",
# kind (uint8), flags (uint8), seg_idx / frame_seq / capture_ts_ms
# as big endian uint32s. 16 bytes total.
BRIDGE_HEADER = struct.Struct(">2sBBIII")

KIND_AUDIO = 1
KIND_PHOTO = 2

# Flag bits (PROTOCOL.md section 5.3). FINAL is bit 1 in both the
# current document and the Swift WireProtocol; the pre-roll bit has
# been assigned both 0x01 (document) and 0x04 (Swift after its field
# mismatch fix), so both are accepted as a pre-roll hint. Flags are
# informational: a frame is never rejected over an unexpected flag.
FLAG_SEGMENT_START = 0x01
FLAG_FINAL = 0x02
FLAG_PREROLL = 0x04
PREROLL_FLAG_MASK = FLAG_SEGMENT_START | FLAG_PREROLL

# Bridge audio payload size: exactly one 100 ms frame of PCM16 mono
# at 16 kHz (PROTOCOL.md section 5.1). Only the final frame of a
# segment may be shorter.
FRAME_BYTES = 3200

DEFAULT_PORT = 8000
DEFAULT_SAMPLE_RATE = 16000


class FrameError(ValueError):
    """A binary frame that must be dropped whole (PROTOCOL.md 4)."""


def _utc_now_iso() -> str:
    """Server wall clock as ISO 8601 UTC with a Z suffix."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_binary_frame(data: bytes) -> dict:
    """Parse one bridge binary frame (PROTOCOL.md section 4).

    Returns a dict with kind, flags, seg_idx, frame_seq,
    capture_ts_ms, and payload. Raises FrameError when the frame is
    shorter than the 16 byte header, the magic is wrong, or the kind
    is not audio or photo: such a frame is dropped whole by callers.
    Legacy "<IIQ" frames do not go through this parser (they carry no
    magic to validate); the ingest handler routes them separately.
    """
    if len(data) < BRIDGE_HEADER.size:
        raise FrameError(
            "binary frame shorter than the 16 byte header: %d bytes" % len(data)
        )
    magic, kind, flags, seg_idx, frame_seq, capture_ts_ms = BRIDGE_HEADER.unpack_from(data, 0)
    if magic != b"OR":
        raise FrameError("bad binary frame magic: %r" % (magic,))
    if kind not in (KIND_AUDIO, KIND_PHOTO):
        raise FrameError("unknown binary frame kind: %d" % kind)
    return {
        "kind": kind,
        "flags": flags,
        "seg_idx": seg_idx,
        "frame_seq": frame_seq,
        "capture_ts_ms": capture_ts_ms,
        "payload": bytes(data[BRIDGE_HEADER.size:]),
    }

# Payload ceilings. Legit traffic is far below every one of these:
# an audio wire frame is 3200 bytes of PCM plus the 16 byte header, a
# cadence photo is a 960 px JPEG, and an enrollment voice sample is
# about 10 seconds of PCM16. The caps exist so one client cannot make
# the server buffer unbounded data in memory (the API has no auth, so
# these bounds are the only thing standing between the LAN and RAM).
MAX_SEARCH_LIMIT = 100
MAX_TEXT_FRAME_BYTES = 64 * 1024
MAX_AUDIO_FRAME_BYTES = 1 * 1024 * 1024
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_ENROLL_PHOTO_BYTES = 10 * 1024 * 1024
MAX_ENROLL_VOICE_BYTES = 16 * 1024 * 1024
# Enrollment voice sample format: raw PCM16 mono at 16 kHz, little
# endian, NO WAV header (the Swift capture path sends int16 samples
# straight from the audio buffer). A voice print needs at least one
# second of audio to embed; enrollment samples are ~10 seconds.
ENROLL_VOICE_BYTES_PER_SECOND = 16000 * 2
MIN_ENROLL_VOICE_BYTES = ENROLL_VOICE_BYTES_PER_SECOND
WAV_MAGIC = b"RIFF"
JPEG_MAGIC = b"\xff\xd8\xff"
# Bridge binary header magic ("OR", see bridge-ios PROTOCOL.md).
BRIDGE_MAGIC = b"OR"


def _clamp_limit(raw: Any, default: int = 10) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_SEARCH_LIMIT))


# --------------------------------------------------------------------------
# Small generic helpers
# --------------------------------------------------------------------------


@dataclasses.dataclass
class _FallbackSettings:
    """Minimal settings used only if onrecord.config cannot be imported."""

    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    sample_rate: int = DEFAULT_SAMPLE_RATE
    data_dir: str = "./data"


def _jsonable(value: Any) -> Any:
    """Convert dataclasses, objects and datetimes into JSON-safe values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        # Binary blobs (PCM, JPEGs) are never part of an API payload.
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return isoformat()
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        out = {}
        for key, val in vars(value).items():
            if key.startswith("_"):
                continue
            out[str(key)] = _jsonable(val)
        return out
    return str(value)


def strip_raw_text(value: Any) -> Any:
    """Recursively drop every raw_text key from dicts and lists."""
    if isinstance(value, dict):
        return {
            k: strip_raw_text(v) for k, v in value.items() if k != "raw_text"
        }
    if isinstance(value, list):
        return [strip_raw_text(v) for v in value]
    return value


def _clean(value: Any) -> Any:
    """JSON-safe copy of a payload with raw_text stripped."""
    return strip_raw_text(_jsonable(value))


def _as_dict(value: Any) -> dict:
    """Best effort conversion of a store or service result into a dict."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return _clean(value)
    if isinstance(value, str):
        return {"id": value}
    converted = _jsonable(value)
    if isinstance(converted, dict):
        return strip_raw_text(converted)
    return {"value": converted}


def _state_str(controller: Any) -> str:
    """Read a SessionController state as an upper-case string."""
    if controller is None:
        return "UNKNOWN"
    for attr in ("state", "status", "current_state"):
        value = getattr(controller, attr, None)
        if value is None:
            continue
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        name = getattr(value, "name", None)
        if isinstance(name, str) and name:
            return name.upper()
        raw = getattr(value, "value", value)
        return str(raw).upper()
    return "UNKNOWN"


def _sealed_duration(sealed: Any, sample_rate: int) -> float:
    """Duration of a sealed segment, from its field or its PCM length."""
    duration = getattr(sealed, "duration_s", None)
    if isinstance(duration, (int, float)) and duration >= 0:
        return float(duration)
    pcm = getattr(sealed, "pcm", None)
    if isinstance(pcm, (bytes, bytearray)) and sample_rate > 0:
        return len(pcm) / float(2 * sample_rate)
    return 0.0


def _import_sibling(module_name: str) -> Any:
    """Import onrecord.<module_name>, returning None when unavailable."""
    try:
        return importlib.import_module("onrecord." + module_name)
    except Exception:
        pass
    try:
        return importlib.import_module("." + module_name, __package__ or "onrecord")
    except Exception:
        return None


def _call_variants(fn: Any, variants: list) -> Any:
    """Call fn with the first argument tuple that does not raise TypeError.

    Used to tolerate small signature differences between the parallel
    implementations that were all written against the shared specs.
    """
    last_exc: Optional[BaseException] = None
    for args, kwargs in variants:
        try:
            return fn(*args, **kwargs)
        except TypeError as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    raise TypeError("no call variants given")


def _factory(module_name: str, attr: str, variants: list) -> Any:
    """Build an object from a sibling module factory, or return None."""
    module = _import_sibling(module_name)
    if module is None:
        return None
    fn = getattr(module, attr, None)
    if fn is None:
        return None
    try:
        return _call_variants(fn, variants)
    except Exception:
        return None


def _load_settings() -> Any:
    settings = _factory("config", "get_settings", [((), {})])
    if settings is None:
        return _FallbackSettings()
    return settings


def _new_controller(session_id: str, sample_rate: int, initial_seq: int = 0) -> Any:
    """Construct a SessionController, tolerating constructor variants.

    initial_seq is the number of segments already stored for the
    session when the controller is (re)built, so a controller created
    after a server restart continues seg numbering where the stored
    session left off instead of reusing seq 0.
    """
    module = _import_sibling("state_machine")
    if module is None:
        raise RuntimeError("onrecord.state_machine is not available")
    cls = getattr(module, "SessionController", None)
    if cls is None:
        raise RuntimeError("onrecord.state_machine.SessionController is missing")
    variants = []
    if initial_seq:
        variants += [
            ((), {"sample_rate": sample_rate, "initial_seq": initial_seq}),
            ((), {"initial_seq": initial_seq}),
        ]
    variants += [
        ((), {"session_id": session_id, "sample_rate": sample_rate}),
        ((), {"sample_rate": sample_rate}),
        ((), {"session_id": session_id}),
        ((), {}),
        ((session_id, sample_rate), {}),
        ((session_id,), {}),
        ((sample_rate,), {}),
    ]
    return _call_variants(cls, variants)


def _build_store(settings: Any) -> Any:
    module = _import_sibling("db")
    if module is None:
        return None
    cls = getattr(module, "Store", None)
    if cls is None:
        return None
    try:
        return _call_variants(cls, [((settings,), {}), ((), {})])
    except Exception:
        return None


def _build_pipeline(
    store: Any,
    vectors: Any,
    transcriber: Any,
    face_emb: Any,
    voice_emb: Any,
    matcher: Any,
    text_embedder: Any,
    sample_rate: int,
) -> Any:
    module = _import_sibling("pipeline")
    if module is None:
        return None
    cls = getattr(module, "Pipeline", None)
    if cls is None:
        return None
    args = (store, vectors, transcriber, face_emb, voice_emb, matcher, text_embedder)
    try:
        return _call_variants(
            cls,
            [
                (args + (sample_rate,), {}),
                (args, {"sample_rate": sample_rate}),
                (args, {}),
            ],
        )
    except Exception:
        return None


def _build_service(module_name: str, attr: str, variants: list) -> Any:
    module = _import_sibling(module_name)
    if module is None:
        return None
    cls = getattr(module, attr, None)
    if cls is None:
        return None
    try:
        return _call_variants(cls, variants)
    except Exception:
        return None


def _first_present(mapping: dict, *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


# --------------------------------------------------------------------------
# App factory
# --------------------------------------------------------------------------


def create_app(
    settings: Any = None,
    store: Any = None,
    vectors: Any = None,
    pipeline: Any = None,
    services: Optional[dict] = None,
    **service_kwargs: Any,
) -> Any:
    """Build the FastAPI app.

    With no arguments, real objects are built from the sibling factories
    (config, db, chroma_store, stt, face, voice, embeddings, identity,
    pipeline); those factories fall back to fakes when the heavy ML
    dependencies are not installed. Any component can be injected instead,
    either positionally (settings, store, vectors, pipeline) or by keyword
    through services / extra kwargs: transcriber, face_embedder (face_emb),
    voice_embedder (voice_emb), text_embedder, matcher (fusion_matcher),
    enrollment_service, query_service, correction_service, build_digest,
    post_digest (slack_poster).
    """
    if not HAS_FASTAPI:
        raise RuntimeError(
            "fastapi is not installed, so the OnRecord server cannot start. "
            "Install it with: pip install fastapi uvicorn"
        )

    overrides: dict = {}
    if services:
        overrides.update(services)
    overrides.update(service_kwargs)

    if settings is None:
        settings = overrides.pop("settings", None)
    if settings is None:
        settings = _load_settings()
    sample_rate = int(getattr(settings, "sample_rate", DEFAULT_SAMPLE_RATE) or DEFAULT_SAMPLE_RATE)

    if store is None:
        store = _first_present(overrides, "store")
    if store is None:
        store = _build_store(settings)

    if vectors is None:
        vectors = _first_present(overrides, "vectors", "vector_store", "vectorstore")
    if vectors is None:
        vectors = _factory(
            "chroma_store",
            "get_vector_store",
            [((settings,), {}), ((), {})],
        )

    transcriber = _first_present(overrides, "transcriber")
    if transcriber is None:
        transcriber = _factory("stt", "get_transcriber", [((settings,), {}), ((), {})])

    face_emb = _first_present(overrides, "face_embedder", "face_emb", "face")
    if face_emb is None:
        face_emb = _factory("face", "get_face_embedder", [((), {}), ((settings,), {})])

    voice_emb = _first_present(overrides, "voice_embedder", "voice_emb", "voice")
    if voice_emb is None:
        voice_emb = _factory("voice", "get_voice_embedder", [((), {}), ((settings,), {})])

    text_embedder = _first_present(overrides, "text_embedder")
    if text_embedder is None:
        text_embedder = _factory(
            "embeddings", "get_text_embedder", [((settings,), {}), ((), {})]
        )

    matcher = _first_present(overrides, "matcher", "fusion_matcher")
    if matcher is None:
        matcher = _build_service(
            "identity",
            "FusionMatcher",
            [((vectors, settings), {}), ((vectors,), {})],
        )

    if pipeline is None:
        pipeline = _first_present(overrides, "pipeline")
    if pipeline is None:
        pipeline = _build_pipeline(
            store, vectors, transcriber, face_emb, voice_emb, matcher,
            text_embedder, sample_rate,
        )

    enrollment_service = _first_present(overrides, "enrollment_service", "enrollment")
    if enrollment_service is None:
        enrollment_service = _build_service(
            "identity",
            "EnrollmentService",
            [
                ((store, vectors, face_emb, voice_emb), {}),
                ((store, vectors), {}),
            ],
        )

    query_service = _first_present(overrides, "query_service", "query")
    if query_service is None:
        query_service = _build_service(
            "query",
            "QueryService",
            [
                ((store, vectors, text_embedder), {}),
                ((store, vectors), {}),
                ((store,), {}),
            ],
        )

    correction_service = _first_present(overrides, "correction_service", "corrections")
    if correction_service is None:
        correction_service = _build_service(
            "corrections", "CorrectionService", [((store,), {})]
        )

    build_digest_fn = _first_present(overrides, "build_digest", "digest_builder")
    post_digest_fn = _first_present(overrides, "post_digest", "slack_poster")

    app = FastAPI(title="OnRecord", version="0.1.0")

    # CORS: the API has no authentication, so cross-origin reads are
    # limited to the local dashboard origin only. A wildcard here
    # would let any website open in the demo browser read transcripts
    # and biometrics metadata from the local server.
    try:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://localhost:3000",
                "http://127.0.0.1:3000",
            ],
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type"],
        )
    except Exception:
        pass

    # Per-session runtime state:
    #   {session_id: {"controller": SessionController,
    #                 "latest_photo": bytes or None,
    #                 "offset": float,          # seconds already processed
    #                 "session": dict,        # last known DB session row
    #                 "processed_count": int}}
    runtime: dict = {}

    app.state.settings = settings
    app.state.store = store
    app.state.vectors = vectors
    app.state.vector_store = vectors
    app.state.pipeline = pipeline
    app.state.enrollment_service = enrollment_service
    app.state.query_service = query_service
    app.state.correction_service = correction_service
    app.state.runtime = runtime
    app.state.sessions_runtime = runtime

    # ------------------------------------------------------------------
    # Internal helpers shared by REST and WebSocket handlers
    # ------------------------------------------------------------------

    def _store_or_503() -> Any:
        if store is None:
            raise HTTPException(status_code=503, detail="session store is unavailable")
        return store

    def _fetch_session(session_id: str, entry: Optional[dict] = None) -> Optional[dict]:
        """Fresh session dict from the store, falling back to the cache."""
        if store is not None:
            getter = getattr(store, "get_session", None)
            if callable(getter):
                try:
                    row = getter(session_id)
                except KeyError:
                    row = None
                except Exception:
                    row = None
                if row is not None:
                    data = _as_dict(row)
                    if entry is not None:
                        entry["session"] = data
                    return data
        if entry is not None and entry.get("session"):
            return dict(entry["session"])
        return None

    def _require_entry(session_id: str) -> dict:
        """Runtime entry for a session, creating one for known DB sessions."""
        entry = runtime.get(session_id)
        if entry is not None:
            return entry
        session = _fetch_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found: " + str(session_id))
        try:
            entry = _make_entry(session_id, session)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        runtime[session_id] = entry
        return entry

    def _make_entry(session_id: str, session: Optional[dict]) -> dict:
        """Build the runtime entry for one session.

        When segments are already stored for the session (the entry is
        being rebuilt after a server restart, or the session was
        driven over REST before the socket connected), the timeline
        offset resumes at the end of the last stored segment and the
        controller starts at the stored segment count, so neither
        timestamps nor seg numbering restart from zero.
        """
        existing = _list_segments(session_id)
        offset = 0.0
        for row in existing:
            end = row.get("end_offset_s")
            if isinstance(end, (int, float)) and float(end) > offset:
                offset = float(end)
        controller = _new_controller(session_id, sample_rate, initial_seq=len(existing))
        return {
            "controller": controller,
            "latest_photo": None,
            "offset": offset,
            "session": session or {"id": session_id},
            "processed_count": len(existing),
            # Sealed segments waiting for the pipeline, in seal order.
            "pending_sealed": [],
            # Explicit timeline gap records (see state_machine gaps).
            "gaps": [],
            "gaps_consumed": 0,
            # Identities of binary frames already accepted on this
            # session, for reconnect replay dedupe (PROTOCOL.md 6).
            "seen_frames": {},
            "last_frame": None,
            "tags": [],
            "source": (session or {}).get("source"),
            "hello_seen": False,
            "conn_seq": 0,
            "connected": False,
            "disconnected_monotonic": None,
            "client_paused": False,
            "client_stats": None,
            "duplicate_frames": 0,
            "malformed_frames": 0,
            "unknown_controls": 0,
        }

    def _note_gap(
        entry: dict,
        kind: str,
        duration_s: Optional[float] = None,
        dropped_bytes: int = 0,
        detail: str = "",
    ) -> None:
        """Record an explicit timeline gap on the session controller."""
        controller = entry.get("controller")
        note = getattr(controller, "note_gap", None)
        if callable(note):
            try:
                note(kind, duration_s=duration_s, dropped_bytes=dropped_bytes, detail=detail)
                return
            except Exception:
                pass
        gaps = getattr(controller, "gaps", None)
        if isinstance(gaps, list):
            gaps.append(
                {
                    "kind": kind,
                    "duration_s": duration_s,
                    "dropped_bytes": int(dropped_bytes or 0),
                    "detail": detail,
                    "after_seq": len(getattr(controller, "segments", []) or []),
                    "closed": True,
                }
            )

    def _collect_gaps(entry: dict, session_id: str) -> list:
        """Move newly closed controller gaps into the entry timeline.

        Each collected gap is stamped with the session timeline offset
        at which it starts, and a measured gap advances the running
        offset, so the segment after a gap is placed after it instead
        of being stitched onto the segment before it. Gaps with an
        unknown duration (reconnect, failover) are recorded but do not
        move the offset. Returns the gaps collected by this call.
        """
        controller = entry.get("controller")
        gaps = getattr(controller, "gaps", None) if controller is not None else None
        if not isinstance(gaps, list):
            return []
        idx = int(entry.get("gaps_consumed") or 0)
        new: list = []
        while idx < len(gaps):
            gap = gaps[idx]
            if not isinstance(gap, dict):
                idx += 1
                continue
            if not gap.get("closed", True):
                # An open gap (a pause still in progress) blocks the
                # queue: later records collect after it closes, which
                # keeps the timeline in open order.
                break
            record = {
                "kind": gap.get("kind"),
                "duration_s": gap.get("duration_s"),
                "dropped_bytes": int(gap.get("dropped_bytes") or 0),
                "detail": gap.get("detail") or "",
                "after_seq": gap.get("after_seq"),
                "start_offset_s": float(entry.get("offset") or 0.0),
            }
            duration = gap.get("duration_s")
            if isinstance(duration, (int, float)) and duration > 0:
                entry["offset"] = float(entry.get("offset") or 0.0) + float(duration)
            entry.setdefault("gaps", []).append(record)
            new.append(record)
            idx += 1
        entry["gaps_consumed"] = idx
        return new

    def _list_segments(session_id: str) -> list:
        if store is None:
            return []
        lister = getattr(store, "list_segments", None)
        if not callable(lister):
            return []
        rows = None
        try:
            rows = lister(session_id)
        except TypeError:
            try:
                rows = lister(session_id=session_id)
            except TypeError:
                try:
                    rows = lister()
                except Exception:
                    rows = None
            except Exception:
                rows = None
        except Exception:
            rows = None
        if not rows:
            return []
        out = []
        for row in rows:
            data = _as_dict(row)
            owner = data.get("session_id")
            if owner is not None and str(owner) != str(session_id):
                continue
            out.append(data)
        return out

    def _segment_count(session_id: str, entry: Optional[dict] = None) -> int:
        segments = _list_segments(session_id)
        if segments:
            return len(segments)
        if entry is not None:
            return int(entry.get("processed_count") or 0)
        return 0

    def _sync_status(entry: dict, session_id: str) -> None:
        """Best effort mirror of the controller state into the DB row."""
        if store is None:
            return
        setter = getattr(store, "set_session_status", None)
        if not callable(setter):
            return
        try:
            setter(session_id, _state_str(entry.get("controller")).lower())
        except Exception:
            pass

    def _end_db_session(session_id: str) -> None:
        if store is None:
            return
        ender = getattr(store, "end_session", None)
        if callable(ender):
            try:
                _call_variants(ender, [((session_id,), {}), ((session_id, "ended"), {})])
                return
            except Exception:
                pass
        setter = getattr(store, "set_session_status", None)
        if callable(setter):
            try:
                setter(session_id, "ended")
            except Exception:
                pass

    def _process_sealed(
        entry: dict, session_id: str, sealed_list: list, strict: bool = True
    ) -> list:
        """Run sealed segments through the pipeline, advancing the offset.

        Every sealed segment is processed with base_offset_s set to the
        running session offset, then the offset advances by the sealed
        duration, so segment timestamps line up across the whole session.
        Returns the new segment dicts with raw_text stripped.

        Sealed segments enter a per-session queue first and are popped
        only after the pipeline accepts them. If the pipeline is
        unavailable (or, with strict=False, a segment fails), the
        unprocessed segments stay queued and drain on a later call
        instead of being dropped on the floor: sealing a segment in
        the controller must never be the moment its audio is lost.
        With strict=True an unavailable pipeline still raises 503, as
        the REST callers have always seen, but the segment is queued
        before the raise.
        """
        pending = entry.setdefault("pending_sealed", [])
        for sealed in sealed_list:
            if sealed is not None:
                pending.append(sealed)
        out: list = []
        while pending:
            if pipeline is None:
                if strict:
                    raise HTTPException(
                        status_code=503, detail="pipeline is unavailable"
                    )
                break
            process = getattr(pipeline, "process_segment", None)
            if not callable(process):
                if strict:
                    raise HTTPException(
                        status_code=503, detail="pipeline.process_segment is unavailable"
                    )
                break
            sealed = pending[0]
            base_offset = float(entry.get("offset") or 0.0)
            photo = entry.get("latest_photo")
            try:
                results = _call_variants(
                    process,
                    [
                        (
                            (),
                            {
                                "session_id": session_id,
                                "sealed": sealed,
                                "base_offset_s": base_offset,
                                "photo_bytes": photo,
                            },
                        ),
                        ((session_id, sealed, base_offset, photo), {}),
                        ((session_id, sealed, base_offset), {}),
                        ((session_id, sealed), {}),
                    ],
                )
            except HTTPException:
                raise
            except Exception:
                if strict:
                    raise
                break
            pending.pop(0)
            if results is None:
                results = []
            elif isinstance(results, dict):
                results = [results]
            cleaned = [_clean(item) for item in results]
            out.extend(cleaned)
            entry["processed_count"] = int(entry.get("processed_count") or 0) + len(cleaned)
            entry["offset"] = base_offset + _sealed_duration(sealed, sample_rate)
        return out

    def _run_action(session_id: str, action: str) -> dict:
        """Drive the controller for one REST action and process any seals."""
        entry = _require_entry(session_id)
        controller = entry.get("controller")
        if controller is None:
            raise HTTPException(status_code=503, detail="session controller is unavailable")
        sealed_list: list = []
        try:
            if action == "arm":
                controller.arm()
            elif action == "start":
                controller.start()
            elif action == "resume":
                controller.resume()
            elif action == "pause":
                sealed = controller.pause()
                if sealed is not None:
                    sealed_list = [sealed]
            elif action == "end_request":
                # Two-step end guard, step one: arms the request on
                # the controller and changes nothing else.
                request_end = getattr(controller, "request_end", None)
                if not callable(request_end):
                    raise HTTPException(
                        status_code=409, detail="controller has no end request step"
                    )
                request_end()
            elif action in ("end", "end_confirm"):
                if action == "end_confirm":
                    confirm = getattr(controller, "confirm_end", None)
                    if not callable(confirm):
                        raise HTTPException(
                            status_code=409, detail="controller has no end confirm step"
                        )
                    result = confirm()
                else:
                    result = controller.end()
                if result is None:
                    sealed_list = []
                elif isinstance(result, (list, tuple)):
                    sealed_list = list(result)
                else:
                    sealed_list = [result]
            else:  # pragma: no cover, routes only pass known actions
                raise HTTPException(status_code=400, detail="unknown action: " + action)
        except HTTPException:
            raise
        except Exception as exc:
            # Anything the state machine rejects is an invalid transition.
            raise HTTPException(status_code=409, detail=str(exc) or exc.__class__.__name__)

        new_segments = _process_sealed(entry, session_id, sealed_list)
        # Gaps collect after the seals of this action are processed:
        # a gap belongs between the segment before it and the segment
        # after it, and collecting here advances the offset for the
        # next segment only.
        new_gaps = _collect_gaps(entry, session_id)

        if action in ("end", "end_confirm"):
            _end_db_session(session_id)
        else:
            _sync_status(entry, session_id)

        session = _fetch_session(session_id, entry)
        if session is None:
            session = dict(entry.get("session") or {"id": session_id})
        return {
            "session": session,
            "state": _state_str(controller),
            "new_segments": new_segments,
            "new_gaps": new_gaps,
            "gaps": list(entry.get("gaps") or []),
            "end_requested": bool(getattr(controller, "end_requested", False)),
        }

    async def _json_body(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _resolve_digest_fns() -> tuple:
        build_fn = build_digest_fn
        if build_fn is not None and not callable(build_fn):
            build_fn = getattr(build_fn, "build_digest", None)
        if build_fn is None:
            module = _import_sibling("digest")
            if module is not None:
                build_fn = getattr(module, "build_digest", None)
        post_fn = post_digest_fn
        if post_fn is not None and not callable(post_fn):
            post_fn = getattr(post_fn, "post_digest", None)
        if post_fn is None:
            module = _import_sibling("slack_poster")
            if module is not None:
                post_fn = getattr(module, "post_digest", None)
        return build_fn, post_fn

    def _do_search(query_text: str, person_ref: Any, limit: int) -> dict:
        limit = _clamp_limit(limit)
        if query_service is None:
            raise HTTPException(status_code=503, detail="query service is unavailable")
        search = getattr(query_service, "search_conversations", None)
        if not callable(search):
            raise HTTPException(status_code=503, detail="query service is unavailable")
        result = _call_variants(
            search,
            [
                ((), {"query": query_text, "person_ref": person_ref, "limit": limit}),
                ((query_text, person_ref, limit), {}),
                ((query_text,), {"limit": limit}),
                ((query_text,), {}),
            ],
        )
        data = _as_dict(result)
        if "results" not in data or not isinstance(data.get("results"), list):
            raw_results = data.get("results")
            data["results"] = raw_results if isinstance(raw_results, list) else []
        if "status" not in data:
            data["status"] = "OK" if data["results"] else "EMPTY"
        if "reason" not in data:
            data["reason"] = None
        return _clean(data)

    # ------------------------------------------------------------------
    # REST: health and session lifecycle
    # ------------------------------------------------------------------

    # /health is an alias for /healthz: docs/SETUP.md, the pre-demo
    # checklist, and scripts/seed_demo.py all probe /health, while the
    # dashboard expects /healthz. Both names must answer.
    @app.get("/healthz")
    @app.get("/health")
    async def healthz() -> dict:
        return {"ok": True}

    @app.post("/sessions")
    @app.post("/api/sessions")
    async def create_session(request: Request) -> dict:
        _store_or_503()
        body = await _json_body(request)
        title = body.get("title")
        source = body.get("source", body.get("capture_source"))
        creator = getattr(store, "create_session", None)
        if not callable(creator):
            raise HTTPException(status_code=503, detail="store.create_session is unavailable")
        created = _call_variants(
            creator,
            [
                ((), {"title": title, "source": source}),
                ((title, source), {}),
                (({"title": title, "source": source},), {}),
                ((), {"title": title}),
                ((title,), {}),
                ((), {}),
            ],
        )
        session = _as_dict(created)
        session_id = session.get("id") or session.get("session_id")
        if session_id is None and isinstance(created, str):
            session_id = created
        if session_id is None:
            raise HTTPException(
                status_code=500, detail="store.create_session did not return a session id"
            )
        session_id = str(session_id)
        session["id"] = session_id
        try:
            runtime[session_id] = _make_entry(session_id, session)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return session

    @app.post("/sessions/{session_id}/arm")
    @app.post("/api/sessions/{session_id}/arm")
    async def session_arm(session_id: str) -> dict:
        return _run_action(session_id, "arm")

    @app.post("/sessions/{session_id}/start")
    @app.post("/api/sessions/{session_id}/start")
    async def session_start(session_id: str) -> dict:
        return _run_action(session_id, "start")

    @app.post("/sessions/{session_id}/pause")
    @app.post("/api/sessions/{session_id}/pause")
    async def session_pause(session_id: str) -> dict:
        return _run_action(session_id, "pause")

    @app.post("/sessions/{session_id}/resume")
    @app.post("/api/sessions/{session_id}/resume")
    async def session_resume(session_id: str) -> dict:
        return _run_action(session_id, "resume")

    @app.post("/sessions/{session_id}/end")
    @app.post("/api/sessions/{session_id}/end")
    async def session_end(session_id: str) -> dict:
        return _run_action(session_id, "end")

    @app.post("/sessions/{session_id}/end_request")
    @app.post("/api/sessions/{session_id}/end_request")
    async def session_end_request(session_id: str) -> dict:
        return _run_action(session_id, "end_request")

    @app.post("/sessions/{session_id}/end_confirm")
    @app.post("/api/sessions/{session_id}/end_confirm")
    async def session_end_confirm(session_id: str) -> dict:
        return _run_action(session_id, "end_confirm")

    @app.get("/sessions/{session_id}/status")
    async def session_status(session_id: str) -> dict:
        entry = runtime.get(session_id)
        session = _fetch_session(session_id, entry)
        if session is None and entry is None:
            raise HTTPException(status_code=404, detail="session not found: " + str(session_id))
        if session is None:
            session = dict(entry.get("session") or {"id": session_id})
        state = _state_str(entry.get("controller")) if entry is not None else str(session.get("status") or "UNKNOWN").upper()
        out = dict(session)
        out["state"] = state
        out["controller_state"] = state
        out["segment_count"] = _segment_count(session_id, entry)
        if entry is not None:
            # Explicit timeline gaps (pauses, frame loss, reconnects),
            # each with the session offset at which it starts.
            _collect_gaps(entry, session_id)
            out["gaps"] = list(entry.get("gaps") or [])
        return _clean(out)

    # ------------------------------------------------------------------
    # REST: dashboard and API surface
    # ------------------------------------------------------------------

    @app.get("/api/sessions")
    async def api_list_sessions() -> list:
        _store_or_503()
        lister = getattr(store, "list_sessions", None)
        if not callable(lister):
            raise HTTPException(status_code=503, detail="store.list_sessions is unavailable")
        rows = lister() or []
        return [_as_dict(row) for row in rows]

    @app.get("/api/sessions/{session_id}")
    async def api_get_session(session_id: str) -> dict:
        entry = runtime.get(session_id)
        session = _fetch_session(session_id, entry)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found: " + str(session_id))
        segments = _list_segments(session_id)
        out = dict(session)
        out["session"] = session
        out["segments"] = segments
        out["segment_count"] = len(segments)
        if entry is not None:
            _collect_gaps(entry, session_id)
            out["gaps"] = list(entry.get("gaps") or [])
            out["tags"] = list(entry.get("tags") or [])
        return _clean(out)

    @app.get("/api/people")
    async def api_list_people() -> list:
        _store_or_503()
        lister = getattr(store, "list_people", None)
        if not callable(lister):
            raise HTTPException(status_code=503, detail="store.list_people is unavailable")
        rows = lister() or []
        return [_as_dict(row) for row in rows]

    @app.delete("/api/people/{person_id}")
    async def api_delete_person(person_id: str) -> dict:
        """Delete a person and all of their biometric and segment data.

        This backs the enrollment consent promise that prints can be
        deleted on request. Removal covers the person row, attributed
        segments and corrections (store), plus the face print, voice
        print, and transcript chunks (vector store).
        """
        _store_or_503()
        result: Any = None
        handled = False
        if enrollment_service is not None:
            deleter = getattr(enrollment_service, "delete_person", None)
            if callable(deleter):
                handled = True
                result = deleter(person_id)
        if not handled:
            store_deleter = getattr(store, "delete_person", None)
            if not callable(store_deleter):
                raise HTTPException(
                    status_code=503, detail="person deletion is unavailable"
                )
            result = store_deleter(person_id)
            if isinstance(result, dict) and vectors is not None:
                segment_ids = result.get("segment_ids") or []
                for collection, key in (
                    ("face_prints", "face:" + str(person_id)),
                    ("voice_prints", "voice:" + str(person_id)),
                ):
                    try:
                        vectors.delete(collection, key)
                    except Exception:
                        pass
                for segment_id in segment_ids:
                    try:
                        vectors.delete("transcript_chunks", segment_id)
                    except Exception:
                        pass
        if result is None:
            raise HTTPException(
                status_code=404, detail="person not found: " + str(person_id)
            )
        segment_ids = []
        if isinstance(result, dict):
            segment_ids = result.get("segment_ids") or []
        return {
            "deleted": True,
            "person_id": str(person_id),
            "segments_deleted": len(segment_ids),
        }

    @app.post("/api/enroll")
    async def api_enroll(request: Request) -> dict:
        body = await _json_body(request)
        return _do_enroll(body)

    @app.post("/api/people/enroll")
    async def api_enroll_alias(request: Request) -> dict:
        body = await _json_body(request)
        return _do_enroll(body)

    def _do_enroll(body: dict) -> dict:
        """Validate the enrollment wire contract and enroll one person.

        Wire format (the Swift bridge codes against this):
            {"name": str, "photo_b64": str, "voice_b64": str, "notes"?: str}
        photo_b64 is base64 of a JPEG photo; voice_b64 is base64 of raw
        PCM16 mono 16 kHz little endian audio with NO WAV header.
        Every violation below is a 422, except oversized payloads which
        are 413, so the phone can tell "fix your payload" apart from
        "something broke server side".
        """
        name = body.get("name")
        photo_b64 = _first_present(body, "photo_b64", "photo", "photo_base64")
        voice_b64 = _first_present(body, "voice_b64", "voice", "voice_base64", "voice_pcm_b64")
        notes = body.get("notes") or ""
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(
                status_code=422,
                detail="enroll requires a non-blank string name",
            )
        if photo_b64 is None or voice_b64 is None:
            raise HTTPException(
                status_code=422,
                detail="enroll requires name, photo_b64 and voice_b64",
            )
        if not isinstance(photo_b64, str) or not isinstance(voice_b64, str):
            raise HTTPException(
                status_code=422,
                detail="photo_b64 and voice_b64 must be base64 strings",
            )
        try:
            photo_bytes = base64.b64decode(photo_b64, validate=True)
            voice_pcm = base64.b64decode(voice_b64, validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="invalid base64 payload: " + str(exc))
        if len(photo_bytes) > MAX_ENROLL_PHOTO_BYTES:
            raise HTTPException(status_code=413, detail="enrollment photo too large")
        if len(voice_pcm) > MAX_ENROLL_VOICE_BYTES:
            raise HTTPException(status_code=413, detail="enrollment voice sample too large")
        if not photo_bytes.startswith(JPEG_MAGIC):
            # Covers both the empty photo and a non-JPEG payload: the
            # face embedder needs a decodable image, so reject early
            # with a clear message instead of a downstream 422.
            raise HTTPException(
                status_code=422,
                detail="photo_b64 must be base64 of a JPEG image",
            )
        if voice_pcm.startswith(WAV_MAGIC):
            raise HTTPException(
                status_code=422,
                detail="voice_b64 must be raw PCM, not a WAV file: strip the WAV header",
            )
        if (
            not voice_pcm
            or len(voice_pcm) % 2 != 0
            or len(voice_pcm) < MIN_ENROLL_VOICE_BYTES
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "voice_b64 must be base64 of raw PCM16 mono 16kHz "
                    "little endian audio, at least %d bytes (%d second)"
                    % (MIN_ENROLL_VOICE_BYTES, MIN_ENROLL_VOICE_BYTES // ENROLL_VOICE_BYTES_PER_SECOND)
                ),
            )
        if enrollment_service is None:
            raise HTTPException(status_code=503, detail="enrollment service is unavailable")
        enroll = getattr(enrollment_service, "enroll_person", None)
        if not callable(enroll):
            raise HTTPException(status_code=503, detail="enrollment service is unavailable")
        try:
            person = _call_variants(
                enroll,
                [
                    ((name, photo_bytes, voice_pcm), {"notes": notes}),
                    ((name, photo_bytes, voice_pcm, notes), {}),
                    (
                        (),
                        {
                            "name": name,
                            "photo_bytes": photo_bytes,
                            "voice_pcm": voice_pcm,
                            "notes": notes,
                        },
                    ),
                ],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc) or "enrollment rejected")
        return _as_dict(person)

    @app.post("/api/search")
    async def api_search(request: Request) -> dict:
        body = await _json_body(request)
        query_text = body.get("query") or body.get("q") or ""
        person_ref = _first_present(body, "person", "person_id", "person_ref")
        try:
            limit = int(body.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        return _do_search(str(query_text), person_ref, limit)

    @app.get("/api/search")
    async def api_search_get(
        q: Optional[str] = None,
        query: Optional[str] = None,
        person: Optional[str] = None,
        person_id: Optional[str] = None,
        limit: int = 10,
    ) -> dict:
        return _do_search(query or q or "", person or person_id, limit)

    @app.post("/api/segments/{segment_id}/correct")
    async def api_correct(segment_id: str, request: Request) -> dict:
        body = await _json_body(request)
        person_id = _first_present(body, "person_id", "personId", "person")
        if person_id is None:
            raise HTTPException(status_code=422, detail="correct requires person_id")
        operator_id = _first_present(body, "operator_id", "operator") or "wearer"
        if correction_service is None:
            raise HTTPException(status_code=503, detail="correction service is unavailable")
        assign = getattr(correction_service, "assign_speaker", None)
        if not callable(assign):
            raise HTTPException(status_code=503, detail="correction service is unavailable")
        try:
            result = _call_variants(
                assign,
                [
                    ((segment_id, person_id, operator_id), {}),
                    (
                        (),
                        {
                            "segment_id": segment_id,
                            "person_id": person_id,
                            "operator_id": operator_id,
                        },
                    ),
                    ((segment_id, person_id), {}),
                ],
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc) or "segment or person not found")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc) or "correction rejected")
        if result is None:
            segment = None
            if store is not None:
                getter = getattr(store, "get_segment", None)
                if callable(getter):
                    try:
                        segment = getter(segment_id)
                    except Exception:
                        segment = None
            if segment is not None:
                return _as_dict(segment)
            return {
                "segment_id": segment_id,
                "speaker_person_id": person_id,
                "attribution_source": "MANUAL",
            }
        return _as_dict(result)

    @app.post("/api/sessions/{session_id}/digest")
    async def api_digest(session_id: str, request: Request) -> dict:
        body = await _json_body(request)
        recap_text = _first_present(body, "recap_text", "recap")
        confirm = bool(body.get("confirm") or body.get("confirmed") or False)
        build_fn, post_fn = _resolve_digest_fns()
        if build_fn is None:
            raise HTTPException(status_code=503, detail="digest builder is unavailable")
        try:
            digest = _call_variants(
                build_fn,
                [
                    ((store, session_id), {"recap_text": recap_text}),
                    ((store, session_id, recap_text), {}),
                    ((store, session_id), {}),
                    ((session_id,), {"recap_text": recap_text}),
                    ((session_id,), {}),
                ],
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc) or "session not found")

        if isinstance(digest, str):
            digest_dict = {"session_id": session_id, "text": digest}
        else:
            digest_dict = _as_dict(digest)
            digest_dict.setdefault("session_id", session_id)

        post_result: Any = None
        if confirm:
            if post_fn is None:
                raise HTTPException(status_code=503, detail="slack poster is unavailable")
            text = (
                digest_dict.get("text")
                or digest_dict.get("digest")
                or digest_dict.get("body")
                or digest_dict.get("message")
                or (digest if isinstance(digest, str) else "")
            )
            post_result = _call_variants(
                post_fn,
                [
                    ((store, session_id, text, settings), {"confirmed": True}),
                    ((store, session_id, text, settings, True), {}),
                    ((store, session_id, text), {"settings": settings, "confirmed": True}),
                    ((session_id, text), {}),
                ],
            )
            post_result = _clean(post_result)

        out = dict(digest_dict)
        out["post_result"] = post_result
        out["post"] = post_result
        out["posted"] = bool(confirm and post_result is not None and post_result is not False)
        return _clean(out)

    # ------------------------------------------------------------------
    # WebSocket ingest
    # ------------------------------------------------------------------

    @app.websocket("/ingest/audio")
    async def ingest_audio(websocket: WebSocket) -> None:
        session_id = websocket.query_params.get("session_id") or websocket.query_params.get("sessionId")
        entry = runtime.get(session_id) if session_id else None
        if entry is None and session_id:
            session = _fetch_session(str(session_id))
            if session is not None:
                try:
                    entry = _make_entry(str(session_id), session)
                    runtime[str(session_id)] = entry
                except Exception:
                    entry = None
        if entry is None or not session_id:
            # Unknown session: PROTOCOL.md section 3.2 has an
            # UNKNOWN_SESSION error for this, and the session must
            # exist server side (created over REST) before the socket
            # is accepted. The close code is pinned by the ingest
            # tests.
            await websocket.close(code=4404)
            return

        session_id = str(session_id)
        controller = entry.get("controller")
        await websocket.accept()

        # One live socket per session. A reconnect with the same
        # session id supersedes the previous socket: the old receive
        # loop sees its connection token go stale and stops, so two
        # sockets can never interleave frames into one controller.
        # The session, controller, and timeline all carry on.
        entry["conn_seq"] = int(entry.get("conn_seq") or 0) + 1
        my_conn = entry["conn_seq"]
        entry["connected"] = True
        photo_pending = False
        conn_hello = False
        ended_cleanly = False

        _sm = _import_sibling("state_machine")
        _invalid = getattr(_sm, "InvalidTransition", None)
        invalid_types = (_invalid,) if isinstance(_invalid, type) else ()

        async def send_error(code: str, message: Any, fatal: bool = False) -> None:
            try:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": code,
                        "message": str(message),
                        "fatal": bool(fatal),
                    }
                )
            except Exception:
                pass

        async def process_and_reply(sealed_list: list) -> list:
            """Process sealed segments, collect gaps, reply with both."""
            try:
                batch = _process_sealed(entry, session_id, sealed_list, strict=False)
            except HTTPException as exc:
                await send_error("SERVER_BUSY", exc.detail or "pipeline is unavailable")
                batch = []
            new_gaps = _collect_gaps(entry, session_id)
            try:
                await websocket.send_json(
                    {"type": "segments", "segments": batch, "gaps": new_gaps}
                )
            except Exception:
                pass
            return batch

        def frame_seen(key: tuple) -> bool:
            return key in (entry.get("seen_frames") or {})

        def frame_mark(key: tuple) -> None:
            seen = entry.setdefault("seen_frames", {})
            seen[key] = True
            # Bounded dedupe memory: identities far behind the live
            # edge cannot be replayed again in practice.
            while len(seen) > 8192:
                seen.pop(next(iter(seen)), None)

        async def handle_bridge_audio(frame: dict) -> None:
            payload = frame["payload"]
            flags = frame["flags"]
            seg_idx = frame["seg_idx"]
            key = (
                "bridge",
                seg_idx,
                frame["frame_seq"],
                frame["capture_ts_ms"],
                len(payload),
            )
            if frame_seen(key):
                # Reconnect replay of a frame the server already has
                # (PROTOCOL.md section 6): drop the duplicate, never
                # append the same audio twice.
                entry["duplicate_frames"] = int(entry.get("duplicate_frames") or 0) + 1
                return
            if len(payload) == 0:
                if flags & FLAG_FINAL:
                    return  # Pure end-of-segment marker, no audio.
                entry["malformed_frames"] = int(entry.get("malformed_frames") or 0) + 1
                await send_error("BAD_PROTOCOL", "audio frame with an empty payload")
                return
            if (
                len(payload) % 2
                or len(payload) > FRAME_BYTES
                or (len(payload) < FRAME_BYTES and not (flags & FLAG_FINAL))
            ):
                # Truncated final sample, an oversize payload, or a
                # short frame that is not the segment's last: the
                # frame is dropped whole and the loss is a timeline
                # gap record, not silent stitching.
                entry["malformed_frames"] = int(entry.get("malformed_frames") or 0) + 1
                _note_gap(
                    entry,
                    "FRAME_DROPPED",
                    dropped_bytes=len(payload),
                    detail="malformed audio frame dropped whole (seg %d, %d payload bytes)"
                    % (seg_idx, len(payload)),
                )
                _collect_gaps(entry, session_id)
                await send_error(
                    "BAD_PROTOCOL", "audio frame payload is truncated or oversize"
                )
                return
            state = _state_str(controller)
            if state in ("IDLE", "ARMED", "PAUSED"):
                # IDLE / ARMED: the controller routes this into the
                # pre-roll ring (server side pre-roll feed). PAUSED:
                # the controller drops and counts it, attributed to
                # the open pause gap. Either way the controller owns
                # the routing; ingest only validates the frame.
                controller.push_audio(payload)
                frame_mark(key)
                return
            if state == "ENDED":
                await send_error("BAD_PROTOCOL", "audio frame after session end")
                return
            # RECORDING.
            expected = getattr(controller, "next_seq", None)
            if expected is not None and seg_idx < expected:
                entry["duplicate_frames"] = int(entry.get("duplicate_frames") or 0) + 1
                _note_gap(
                    entry,
                    "LATE_FRAME",
                    dropped_bytes=len(payload),
                    detail="frame for already sealed segment %d dropped" % seg_idx,
                )
                return
            if expected is not None and seg_idx > expected:
                entry["malformed_frames"] = int(entry.get("malformed_frames") or 0) + 1
                await send_error(
                    "BAD_PROTOCOL",
                    "audio frame for segment %d before its talk_start" % seg_idx,
                )
                return
            last = entry.get("last_frame")
            if isinstance(last, dict) and last.get("seg_idx") == seg_idx:
                expected_ts = int(last["capture_ts_ms"]) + int(last["duration_ms"])
                capture_ts = int(frame["capture_ts_ms"])
                if capture_ts > expected_ts + 50:
                    missing_ms = capture_ts - expected_ts
                    _note_gap(
                        entry,
                        "FRAME_GAP",
                        duration_s=missing_ms / 1000.0,
                        dropped_bytes=int(missing_ms * sample_rate * 2 / 1000),
                        detail="%d ms of audio missing inside segment %d"
                        % (missing_ms, seg_idx),
                    )
            controller.push_audio(payload)
            frame_mark(key)
            entry["last_frame"] = {
                "seg_idx": seg_idx,
                "capture_ts_ms": int(frame["capture_ts_ms"]),
                "duration_ms": int(len(payload) * 1000 / (sample_rate * 2)),
            }

        async def handle_bridge_photo(frame: dict) -> None:
            payload = frame["payload"]
            if len(payload) > MAX_PHOTO_BYTES:
                entry["malformed_frames"] = int(entry.get("malformed_frames") or 0) + 1
                await send_error("BAD_PROTOCOL", "photo frame too large")
                return
            if not payload.startswith(JPEG_MAGIC):
                entry["malformed_frames"] = int(entry.get("malformed_frames") or 0) + 1
                await send_error("BAD_PROTOCOL", "photo frame is not a JPEG")
                return
            entry["latest_photo"] = payload

        while True:
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                break
            except RuntimeError:
                break
            if not isinstance(message, dict):
                continue
            if message.get("type") == "websocket.disconnect":
                break
            if entry.get("conn_seq") != my_conn:
                # Superseded by a newer connection for this session.
                break

            try:
                text_frame = message.get("text")
                bytes_frame = message.get("bytes")

                if text_frame is not None:
                    if len(text_frame) > MAX_TEXT_FRAME_BYTES:
                        await send_error("BAD_PROTOCOL", "control frame too large")
                        continue
                    try:
                        payload = json.loads(text_frame)
                    except (ValueError, TypeError) as exc:
                        await send_error(
                            "BAD_PROTOCOL", "invalid JSON control frame: " + str(exc)
                        )
                        continue
                    if not isinstance(payload, dict):
                        await send_error(
                            "BAD_PROTOCOL", "control frame must be a JSON object"
                        )
                        continue
                    control = payload.get("type")
                    state = _state_str(controller)

                    # A control message naming a different session is
                    # a protocol violation for that message only.
                    msg_sid = payload.get("session_id")
                    if (
                        control != "hello"
                        and msg_sid is not None
                        and str(msg_sid) != session_id
                    ):
                        await send_error(
                            "BAD_PROTOCOL",
                            "control frame session_id does not match the connection",
                        )
                        continue

                    if control == "hello":
                        proto = payload.get("protocol", 1)
                        hello_sid = payload.get("session_id") or payload.get(
                            "resume_session_id"
                        )
                        if proto != 1 or (
                            hello_sid is not None and str(hello_sid) != session_id
                        ):
                            await send_error(
                                "BAD_PROTOCOL",
                                "hello rejected: unsupported protocol or session mismatch",
                                fatal=True,
                            )
                            try:
                                await websocket.close()
                            except Exception:
                                pass
                            ended_cleanly = True
                            break
                        if entry.get("hello_seen") and entry.get("hello_conn") != my_conn:
                            # Reconnect for the same session: a resume,
                            # never a new session (PROTOCOL.md 2 and 6).
                            _note_gap(
                                entry,
                                "RECONNECT",
                                detail="websocket reconnected; same session resumed",
                            )
                            source = payload.get("source") or payload.get("audio_source")
                            prev_source = entry.get("source")
                            if (
                                source
                                and prev_source
                                and str(source) != str(prev_source)
                            ):
                                _note_gap(
                                    entry,
                                    "SOURCE_FAILOVER",
                                    detail="capture source changed %s -> %s; session continued"
                                    % (prev_source, source),
                                )
                        source = payload.get("source") or payload.get("audio_source")
                        if source:
                            entry["source"] = str(source)
                        entry["hello_seen"] = True
                        entry["hello_conn"] = my_conn
                        conn_hello = True
                        await websocket.send_json(
                            {
                                "type": "welcome",
                                "session_id": session_id,
                                "server_time": _utc_now_iso(),
                                "ok": True,
                            }
                        )

                    elif control == "ping":
                        t_mono = payload.get(
                            "t0_mono_ms", payload.get("t_mono_ms", payload.get("t"))
                        )
                        await websocket.send_json(
                            {
                                "type": "pong",
                                "t0_mono_ms": t_mono,
                                "t_mono_ms": t_mono,
                                "server_time": _utc_now_iso(),
                            }
                        )

                    elif control == "session_start":
                        # The phone arms locally, then announces the
                        # session on the socket. Arm server side if
                        # needed; a duplicate announcement is a no-op.
                        if state == "IDLE":
                            controller.arm()
                        elif state == "ENDED":
                            await send_error(
                                "BAD_PROTOCOL", "session_start after session end"
                            )

                    elif control == "talk_start":
                        if state == "IDLE":
                            controller.arm()
                            controller.start()
                        elif state == "ARMED":
                            controller.start()
                        elif state == "RECORDING":
                            # Already recording: this opens the next
                            # segment on the continuous server model.
                            # A seg_idx that disagrees with the open
                            # segment is recorded, not silently
                            # stitched over.
                            seg_idx = payload.get("seg_idx")
                            expected = getattr(controller, "next_seq", None)
                            if (
                                isinstance(seg_idx, int)
                                and expected is not None
                                and seg_idx != expected
                            ):
                                _note_gap(
                                    entry,
                                    "SEGMENT_MISMATCH",
                                    detail="talk_start seg_idx %s, server open segment %s"
                                    % (seg_idx, expected),
                                )
                        else:
                            await send_error(
                                "BAD_PROTOCOL", "talk_start in state " + state
                            )

                    elif control in ("talk_end", "talk_stop", "new_segment"):
                        if state == "RECORDING":
                            try:
                                sealed = controller.new_segment()
                            except invalid_types:
                                # Nothing was captured for this
                                # segment (or this is a replayed
                                # talk_end): the empty segment is a
                                # gap record, not a fake segment.
                                _note_gap(
                                    entry,
                                    "EMPTY_SEGMENT",
                                    detail="talk_end with no audio captured for the segment",
                                )
                                await process_and_reply([])
                            else:
                                await process_and_reply([sealed])
                        elif state in ("ARMED", "PAUSED"):
                            await process_and_reply([])
                        else:
                            await send_error(
                                "BAD_PROTOCOL", "talk_end in state " + state
                            )

                    elif control in ("pause", "session_pause"):
                        if state == "RECORDING":
                            sealed = controller.pause()
                            await process_and_reply(
                                [sealed] if sealed is not None else []
                            )
                        elif state == "ARMED":
                            # The controller graph has no ARMED pause
                            # (pinned by the contract tests); the
                            # adapter records the client pause so a
                            # resume can pair with it.
                            entry["client_paused"] = True
                            await process_and_reply([])
                        elif state == "PAUSED":
                            await process_and_reply([])
                        else:
                            await send_error(
                                "BAD_PROTOCOL", "pause in state " + state
                            )

                    elif control in ("resume", "session_resume"):
                        if state == "PAUSED":
                            controller.resume()
                            entry["client_paused"] = False
                            # Close the pause gap into the offset now,
                            # before any later segment is processed.
                            _collect_gaps(entry, session_id)
                        elif entry.get("client_paused"):
                            entry["client_paused"] = False
                        elif state == "RECORDING":
                            pass  # Duplicate or replayed resume.
                        else:
                            await send_error(
                                "BAD_PROTOCOL", "resume in state " + state
                            )

                    elif control == "arm":
                        controller.arm()
                    elif control == "start":
                        controller.start()
                    elif control == "photo":
                        inline = _first_present(payload, "photo_b64", "data", "photo")
                        if inline is not None:
                            if not isinstance(inline, str):
                                raise ValueError("inline photo must be a base64 string")
                            try:
                                decoded = base64.b64decode(inline, validate=True)
                            except (binascii.Error, ValueError, TypeError) as exc:
                                raise ValueError("invalid base64 photo: " + str(exc))
                            if len(decoded) > MAX_PHOTO_BYTES:
                                raise ValueError("photo too large")
                            if not decoded.startswith(JPEG_MAGIC):
                                raise ValueError("photo is not a JPEG")
                            entry["latest_photo"] = decoded
                            photo_pending = False
                        else:
                            # The next binary frame on this socket is a JPEG.
                            photo_pending = True

                    elif control == "photo_meta":
                        # Metadata for the photo binary frame that
                        # follows it (PROTOCOL.md 5.2). The frame
                        # itself is self describing; this is kept for
                        # the record only.
                        entry["last_photo_meta"] = {
                            "photo_id": payload.get("photo_id"),
                            "seq": payload.get("seq"),
                            "captured_at": payload.get("captured_at"),
                            "eager": payload.get("eager"),
                            "bytes": payload.get("bytes"),
                        }

                    elif control == "tag_moment":
                        entry.setdefault("tags", []).append(
                            {
                                "t_mono_ms": payload.get(
                                    "t_mono_ms", payload.get("t0_mono_ms")
                                ),
                                "source": payload.get("source"),
                            }
                        )

                    elif control in ("end", "session_end"):
                        stats = payload.get("stats")
                        if isinstance(stats, dict):
                            entry["client_stats"] = stats
                            staged_dropped = stats.get("chunks_dropped")
                            if isinstance(staged_dropped, int) and staged_dropped > 0:
                                _note_gap(
                                    entry,
                                    "STAGED_DROPS",
                                    detail="%d staged frames were evicted on the phone before replay"
                                    % staged_dropped,
                                )
                        sealed_list: list = []
                        if state in ("ARMED", "RECORDING", "PAUSED"):
                            result = controller.end()
                            if isinstance(result, (list, tuple)):
                                sealed_list = list(result)
                            elif result is not None:
                                sealed_list = [result]
                        try:
                            batch = _process_sealed(
                                entry, session_id, sealed_list, strict=False
                            )
                        except HTTPException as exc:
                            await send_error(
                                "SERVER_BUSY", exc.detail or "pipeline is unavailable"
                            )
                            batch = []
                        new_gaps = _collect_gaps(entry, session_id)
                        if batch:
                            await websocket.send_json(
                                {
                                    "type": "segments",
                                    "segments": batch,
                                    "gaps": new_gaps,
                                }
                            )
                        _end_db_session(session_id)
                        await websocket.send_json({"type": "session_ended"})
                        ended_cleanly = True
                        try:
                            await websocket.close()
                        except Exception:
                            pass
                        break

                    elif control == "capture_now":
                        # Server to client only; a client sending it
                        # is ignored like any other unknown type.
                        entry["unknown_controls"] = (
                            int(entry.get("unknown_controls") or 0) + 1
                        )
                    else:
                        # PROTOCOL.md section 3: unknown message types
                        # must be ignored, not treated as errors.
                        entry["unknown_controls"] = (
                            int(entry.get("unknown_controls") or 0) + 1
                        )

                elif bytes_frame is not None:
                    data = bytes(bytes_frame)
                    if len(data) >= 2 and data[:2] == BRIDGE_MAGIC:
                        # Bridge frame (PROTOCOL.md section 4). This
                        # check comes before the legacy photo_pending
                        # path: a typed frame describes itself, and a
                        # photo frame must never be routed into the
                        # audio path as PCM.
                        try:
                            frame = parse_binary_frame(data)
                        except FrameError as exc:
                            entry["malformed_frames"] = (
                                int(entry.get("malformed_frames") or 0) + 1
                            )
                            await send_error("BAD_PROTOCOL", str(exc))
                            continue
                        if not conn_hello:
                            entry["malformed_frames"] = (
                                int(entry.get("malformed_frames") or 0) + 1
                            )
                            await send_error(
                                "BAD_PROTOCOL",
                                "binary frame received before hello on this connection",
                            )
                            continue
                        if frame["kind"] == KIND_PHOTO:
                            await handle_bridge_photo(frame)
                        else:
                            await handle_bridge_audio(frame)
                    elif photo_pending:
                        photo = data
                        if len(photo) > MAX_PHOTO_BYTES:
                            raise ValueError("photo frame too large")
                        if not photo.startswith(JPEG_MAGIC):
                            raise ValueError("photo frame is not a JPEG")
                        entry["latest_photo"] = photo
                        photo_pending = False
                    elif not (len(data) >= 2 and data[1] == 0):
                        # Not a bridge frame (no magic) and not a
                        # plausible legacy frame either: a legacy
                        # header starts with the low bytes of a small
                        # seg_idx, so its second byte is 0. Anything
                        # else is a bridge frame with corrupted magic
                        # and is dropped whole (PROTOCOL.md section 4).
                        entry["malformed_frames"] = (
                            int(entry.get("malformed_frames") or 0) + 1
                        )
                        await send_error(
                            "BAD_PROTOCOL", "bad binary frame magic; frame dropped"
                        )
                        continue
                    else:
                        # Legacy frame: 16 byte "<IIQ" header plus PCM.
                        if len(data) > MAX_AUDIO_FRAME_BYTES + AUDIO_HEADER.size:
                            raise ValueError("audio frame too large")
                        if len(data) < AUDIO_HEADER.size:
                            raise ValueError(
                                "audio frame shorter than the 16 byte header"
                            )
                        seg_idx, frame_seq, capture_ts_ms = AUDIO_HEADER.unpack_from(data, 0)
                        legacy_payload = data[AUDIO_HEADER.size:]
                        legacy_key = (
                            "legacy",
                            seg_idx,
                            frame_seq,
                            capture_ts_ms,
                            len(legacy_payload),
                        )
                        if frame_seen(legacy_key):
                            entry["duplicate_frames"] = (
                                int(entry.get("duplicate_frames") or 0) + 1
                            )
                            continue
                        controller.push_audio(legacy_payload)
                        frame_mark(legacy_key)

            except WebSocketDisconnect:
                break
            except HTTPException as exc:
                await send_error("SERVER_BUSY", exc.detail or "server busy")
            except invalid_types as exc:
                await send_error("INVALID_TRANSITION", str(exc) or "invalid transition")
            except Exception as exc:
                await send_error(
                    "BAD_PROTOCOL", str(exc) or exc.__class__.__name__
                )

        entry["connected"] = False
        if not ended_cleanly and entry.get("conn_seq") == my_conn:
            # Dropped without session_end: the session stays open on
            # the server (same id, same controller, same timeline)
            # and a reconnect hello resumes it. Nothing is sealed or
            # discarded just because the socket went away.
            entry["disconnected_monotonic"] = time.monotonic()

    return app


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main() -> None:
    """Serve the app with uvicorn: python -m onrecord.ingest."""
    if not HAS_FASTAPI:
        raise RuntimeError(
            "fastapi is not installed, so the OnRecord server cannot start. "
            "Install it with: pip install fastapi uvicorn"
        )
    import uvicorn

    settings = _load_settings()
    port = int(getattr(settings, "port", DEFAULT_PORT) or DEFAULT_PORT)
    # Bind only where config says (HOST env, default 127.0.0.1). The
    # API has no auth; binding 0.0.0.0 would expose transcripts,
    # enrollment, and deletion to every network the laptop joins.
    host = str(getattr(settings, "host", None) or "127.0.0.1")
    app = create_app(settings=settings)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
