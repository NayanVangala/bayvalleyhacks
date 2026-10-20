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
- POST /api/search                 {query, person?, limit?}
- POST /api/segments/{id}/correct  {person_id, operator_id?}
- POST /api/sessions/{id}/digest   {recap_text?, confirm?}

WebSocket ingest: WS /ingest/audio?session_id=<id>

Text frames are JSON control messages:

    {"type": "arm" | "start" | "talk_stop" | "new_segment" | "pause"
             | "resume" | "photo" | "end"}

"photo" means the NEXT binary frame on the socket is a JPEG photo. The photo
is kept as the session's latest photo and handed to the pipeline with every
segment sealed afterwards, so face identification can use it.

Binary audio frames have a 16-byte header, struct format "<IIQ":

    seg_idx        uint32, little endian
    frame_seq      uint32, little endian
    capture_ts_ms  uint64, little endian, milliseconds since epoch

followed by raw PCM16 mono audio at the configured sample rate (default
16000 Hz). The header is stripped before the PCM is pushed into the
SessionController.

Server to client messages on the ingest socket:

    {"type": "segments", "segments": [...]}   after each sealed batch
    {"type": "session_ended"}                 after an "end" control frame
    {"type": "error", "message": "..."}       per-message failure, socket
                                              stays open

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
import importlib
import json
import struct
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

__all__ = ["HAS_FASTAPI", "AUDIO_HEADER", "create_app", "main", "strip_raw_text"]

# Binary audio frame header: seg_idx (uint32), frame_seq (uint32),
# capture_ts_ms (uint64), all little endian. 16 bytes total.
AUDIO_HEADER = struct.Struct("<IIQ")

DEFAULT_PORT = 8000
DEFAULT_SAMPLE_RATE = 16000


# --------------------------------------------------------------------------
# Small generic helpers
# --------------------------------------------------------------------------


@dataclasses.dataclass
class _FallbackSettings:
    """Minimal settings used only if onrecord.config cannot be imported."""

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


def _new_controller(session_id: str, sample_rate: int) -> Any:
    """Construct a SessionController, tolerating constructor variants."""
    module = _import_sibling("state_machine")
    if module is None:
        raise RuntimeError("onrecord.state_machine is not available")
    cls = getattr(module, "SessionController", None)
    if cls is None:
        raise RuntimeError("onrecord.state_machine.SessionController is missing")
    return _call_variants(
        cls,
        [
            ((), {"session_id": session_id, "sample_rate": sample_rate}),
            ((), {"sample_rate": sample_rate}),
            ((), {"session_id": session_id}),
            ((), {}),
            ((session_id, sample_rate), {}),
            ((session_id,), {}),
            ((sample_rate,), {}),
        ],
    )


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
            controller = _new_controller(session_id, sample_rate)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        entry = {
            "controller": controller,
            "latest_photo": None,
            "offset": 0.0,
            "session": session,
            "processed_count": 0,
        }
        runtime[session_id] = entry
        return entry

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

    def _process_sealed(entry: dict, session_id: str, sealed_list: list) -> list:
        """Run sealed segments through the pipeline, advancing the offset.

        Every sealed segment is processed with base_offset_s set to the
        running session offset, then the offset advances by the sealed
        duration, so segment timestamps line up across the whole session.
        Returns the new segment dicts with raw_text stripped.
        """
        out: list = []
        for sealed in sealed_list:
            if sealed is None:
                continue
            if pipeline is None:
                raise HTTPException(
                    status_code=503, detail="pipeline is unavailable"
                )
            process = getattr(pipeline, "process_segment", None)
            if not callable(process):
                raise HTTPException(
                    status_code=503, detail="pipeline.process_segment is unavailable"
                )
            base_offset = float(entry.get("offset") or 0.0)
            photo = entry.get("latest_photo")
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
            elif action == "end":
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

        if action == "end":
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

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.post("/sessions")
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
            controller = _new_controller(session_id, sample_rate)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        runtime[session_id] = {
            "controller": controller,
            "latest_photo": None,
            "offset": 0.0,
            "session": session,
            "processed_count": 0,
        }
        return session

    @app.post("/sessions/{session_id}/arm")
    async def session_arm(session_id: str) -> dict:
        return _run_action(session_id, "arm")

    @app.post("/sessions/{session_id}/start")
    async def session_start(session_id: str) -> dict:
        return _run_action(session_id, "start")

    @app.post("/sessions/{session_id}/pause")
    async def session_pause(session_id: str) -> dict:
        return _run_action(session_id, "pause")

    @app.post("/sessions/{session_id}/resume")
    async def session_resume(session_id: str) -> dict:
        return _run_action(session_id, "resume")

    @app.post("/sessions/{session_id}/end")
    async def session_end(session_id: str) -> dict:
        return _run_action(session_id, "end")

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
        return _clean(out)

    @app.get("/api/people")
    async def api_list_people() -> list:
        _store_or_503()
        lister = getattr(store, "list_people", None)
        if not callable(lister):
            raise HTTPException(status_code=503, detail="store.list_people is unavailable")
        rows = lister() or []
        return [_as_dict(row) for row in rows]

    @app.post("/api/enroll")
    async def api_enroll(request: Request) -> dict:
        body = await _json_body(request)
        return _do_enroll(body)

    @app.post("/api/people/enroll")
    async def api_enroll_alias(request: Request) -> dict:
        body = await _json_body(request)
        return _do_enroll(body)

    def _do_enroll(body: dict) -> dict:
        name = body.get("name")
        photo_b64 = _first_present(body, "photo_b64", "photo", "photo_base64")
        voice_b64 = _first_present(body, "voice_b64", "voice", "voice_base64", "voice_pcm_b64")
        notes = body.get("notes") or ""
        if not name or photo_b64 is None or voice_b64 is None:
            raise HTTPException(
                status_code=422,
                detail="enroll requires name, photo_b64 and voice_b64",
            )
        try:
            photo_bytes = base64.b64decode(photo_b64)
            voice_pcm = base64.b64decode(voice_b64)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail="invalid base64 payload: " + str(exc))
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
                    entry = {
                        "controller": _new_controller(str(session_id), sample_rate),
                        "latest_photo": None,
                        "offset": 0.0,
                        "session": session,
                        "processed_count": 0,
                    }
                    runtime[str(session_id)] = entry
                except Exception:
                    entry = None
        if entry is None or not session_id:
            await websocket.close(code=4404)
            return

        session_id = str(session_id)
        controller = entry.get("controller")
        await websocket.accept()
        photo_pending = False

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

            try:
                text_frame = message.get("text")
                bytes_frame = message.get("bytes")

                if text_frame is not None:
                    try:
                        payload = json.loads(text_frame)
                    except (ValueError, TypeError) as exc:
                        raise ValueError("invalid JSON control frame: " + str(exc))
                    if not isinstance(payload, dict):
                        raise ValueError("control frame must be a JSON object")
                    control = payload.get("type")

                    if control == "arm":
                        controller.arm()
                    elif control == "start":
                        controller.start()
                    elif control == "resume":
                        controller.resume()
                    elif control == "pause":
                        sealed = controller.pause()
                        batch = _process_sealed(
                            entry, session_id, [sealed] if sealed is not None else []
                        )
                        await websocket.send_json({"type": "segments", "segments": batch})
                    elif control in ("talk_stop", "new_segment"):
                        sealed = controller.new_segment()
                        batch = _process_sealed(
                            entry, session_id, [sealed] if sealed is not None else []
                        )
                        await websocket.send_json({"type": "segments", "segments": batch})
                    elif control == "photo":
                        inline = _first_present(payload, "photo_b64", "data", "photo")
                        if inline is not None:
                            try:
                                entry["latest_photo"] = base64.b64decode(inline)
                            except (binascii.Error, ValueError) as exc:
                                raise ValueError("invalid base64 photo: " + str(exc))
                            photo_pending = False
                        else:
                            # The next binary frame on this socket is a JPEG.
                            photo_pending = True
                    elif control == "end":
                        result = controller.end()
                        if result is None:
                            sealed_list = []
                        elif isinstance(result, (list, tuple)):
                            sealed_list = list(result)
                        else:
                            sealed_list = [result]
                        batch = _process_sealed(entry, session_id, sealed_list)
                        if batch:
                            await websocket.send_json(
                                {"type": "segments", "segments": batch}
                            )
                        _end_db_session(session_id)
                        await websocket.send_json({"type": "session_ended"})
                        try:
                            await websocket.close()
                        except Exception:
                            pass
                        return
                    else:
                        raise ValueError("unknown control type: " + str(control))

                elif bytes_frame is not None:
                    data = bytes(bytes_frame)
                    if photo_pending:
                        entry["latest_photo"] = data
                        photo_pending = False
                    else:
                        if len(data) < AUDIO_HEADER.size:
                            raise ValueError(
                                "audio frame shorter than the 16 byte header"
                            )
                        seg_idx, frame_seq, capture_ts_ms = AUDIO_HEADER.unpack_from(data, 0)
                        # Header fields are bookkeeping for gap detection in
                        # the controller and pipeline; the PCM payload is
                        # everything after the 16 byte header.
                        _ = (seg_idx, frame_seq, capture_ts_ms)
                        controller.push_audio(data[AUDIO_HEADER.size:])

            except WebSocketDisconnect:
                break
            except Exception as exc:
                try:
                    await websocket.send_json(
                        {"type": "error", "message": str(exc) or exc.__class__.__name__}
                    )
                except Exception:
                    break

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
    app = create_app(settings=settings)
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
