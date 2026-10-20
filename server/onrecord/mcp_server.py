"""MCP server for OnRecord, served over stdio as "insight-mcp".

Read-only by design: every tool only reads stored conversations, people,
and search indexes. There are deliberately no write tools, so a client
connected to this server can never create, edit, or delete OnRecord data.

Protocol: JSON-RPC 2.0, one JSON message per line on stdin/stdout, the
MCP stdio transport shape (protocol version 2024-11-05). Implemented
directly with the standard library only; no external MCP SDK.

The QueryService and its dependencies are built lazily on the first
tools/call, so initialize and tools/list work even before the service
layer can be constructed.
"""

from __future__ import annotations

import dataclasses
import json
import sys

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "insight-mcp"
SERVER_VERSION = "0.1.0"

ERR_METHOD_NOT_FOUND = -32601

REASON_PERSON_NOT_FOUND = "PERSON_NOT_FOUND"
REASON_SESSION_NOT_FOUND = "SESSION_NOT_FOUND"


def _jsonable(value):
    """Convert dataclasses, dicts, and plain objects into JSON-safe data."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return {key: _jsonable(item) for key, item in vars(value).items()}
    return str(value)


def _construct(factory, settings):
    """Call a factory or constructor that may or may not take settings."""
    try:
        return factory(settings)
    except TypeError:
        return factory()


def build_service():
    """Build the QueryService and return a (service, store) pair.

    Settings come from onrecord.config.get_settings, the vector store
    from onrecord.chroma_store.get_vector_store, and the text embedder
    from onrecord.embeddings.get_text_embedder. Those factories fall
    back to lightweight fakes when ML dependencies are unavailable.
    """
    from onrecord.chroma_store import get_vector_store
    from onrecord.config import get_settings
    from onrecord.embeddings import get_text_embedder
    from onrecord.query import QueryService

    try:
        from onrecord.store import Store
    except ImportError:
        from onrecord.db import Store

    settings = get_settings()
    store = _construct(Store, settings)
    vectors = _construct(get_vector_store, settings)
    text_embedder = _construct(get_text_embedder, settings)
    service = QueryService(store, vectors, text_embedder)
    if getattr(service, "store", None) is None:
        try:
            service.store = store
        except Exception:
            pass
    return service, store


class ToolError(Exception):
    """A tool failure whose message is safe to return to the client."""


TOOL_DEFINITIONS = [
    {
        "name": "list_people",
        "description": "List every enrolled person known to OnRecord.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "list_interactions_with_person",
        "description": (
            "List the sessions that include a person, with the number of "
            "segments attributed to that person in each session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "person": {
                    "type": "string",
                    "description": "Person id or name.",
                }
            },
            "required": ["person"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_conversations",
        "description": (
            "Semantic search over stored conversation segments. Returns "
            "verbatim quotes with speaker and timestamps. An empty result "
            "carries a reason code instead of failing silently."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "person": {
                    "type": "string",
                    "description": "Optional person id or name to restrict to.",
                },
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_interaction",
        "description": "Fetch one session (interaction) by its session id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_person_profile",
        "description": "Fetch the profile for one person by id or name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "person": {
                    "type": "string",
                    "description": "Person id or name.",
                }
            },
            "required": ["person"],
            "additionalProperties": False,
        },
    },
]


class InsightMcpServer:
    """JSON-RPC 2.0 loop plus the read-only tool implementations."""

    def __init__(self):
        self._service = None
        self._store = None
        self._service_error = None

    # -- service access ------------------------------------------------

    def service_and_store(self):
        if self._service is None and self._service_error is None:
            try:
                self._service, self._store = build_service()
            except Exception as exc:  # missing module, bad config, etc.
                self._service_error = exc
        if self._service is None:
            raise ToolError(
                "SERVICE_UNAVAILABLE: %s: %s"
                % (type(self._service_error).__name__, self._service_error)
            )
        return self._service, self._store

    # -- tool helpers ----------------------------------------------------

    @staticmethod
    def _require(arguments, key):
        value = arguments.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ToolError("MISSING_ARGUMENT: %s" % key)
        # All required arguments in this server are strings. Reject
        # other JSON types instead of letting them flow into the query
        # layer, where a list or dict would crash or coerce oddly.
        if not isinstance(value, str):
            raise ToolError("INVALID_ARGUMENT: %s must be a string" % key)
        if len(value) > 2000:
            raise ToolError("INVALID_ARGUMENT: %s is too long" % key)
        return value

    @staticmethod
    def _limit(arguments, default):
        raw = arguments.get("limit")
        if raw is None:
            return default
        # bool is a subclass of int; True is not a limit. A float is
        # accepted only when it is integral (JSON clients vary here).
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ToolError("INVALID_ARGUMENT: limit must be an integer")
        if isinstance(raw, float) and not raw.is_integer():
            raise ToolError("INVALID_ARGUMENT: limit must be an integer")
        raw = int(raw)
        # Clamp: an unbounded limit lets one tools/call force an
        # arbitrarily large vector query and response.
        return max(1, min(raw, 100))

    def _resolve_person(self, store, person_ref):
        """Resolve a person ref (id or name) against the store's people."""
        people = _jsonable(store.list_people())
        ref = str(person_ref).strip()
        for person in people:
            if str(person.get("id")) == ref:
                return person
        lowered = ref.lower()
        for person in people:
            if str(person.get("name", "")).lower() == lowered:
                return person
        return None

    def _interactions_via_store(self, store, person_ref):
        """Compose list_interactions_with_person from store primitives."""
        person = self._resolve_person(store, person_ref)
        if person is None:
            raise ToolError(REASON_PERSON_NOT_FOUND)
        segments = _jsonable(store.segments_for_person(person["id"]))
        by_session = {}
        for segment in segments:
            session_id = segment.get("session_id")
            if session_id is not None:
                by_session.setdefault(session_id, []).append(segment)
        interactions = []
        for session_id, person_segments in by_session.items():
            session = store.get_session(session_id)
            interactions.append(
                {
                    "session_id": session_id,
                    "session": _jsonable(session) if session is not None else None,
                    "segment_count": len(person_segments),
                }
            )
        interactions.sort(
            key=lambda item: str((item.get("session") or {}).get("started_at", ""))
        )
        return {
            "person": person,
            "session_count": len(interactions),
            "segment_count": len(segments),
            "interactions": interactions,
        }

    # -- tools -----------------------------------------------------------

    def call_tool(self, name, arguments):
        """Run one tool and return its plain (JSON-safe) result."""
        arguments = arguments or {}
        known = {tool["name"] for tool in TOOL_DEFINITIONS}
        if name not in known:
            raise ToolError("UNKNOWN_TOOL: %s" % name)
        service, store = self.service_and_store()

        if name == "list_people":
            return _jsonable(service.list_people())

        if name == "list_interactions_with_person":
            person = self._require(arguments, "person")
            method = getattr(service, "list_interactions_with_person", None)
            if callable(method):
                return _jsonable(method(person))
            return self._interactions_via_store(store, person)

        if name == "search_conversations":
            query = self._require(arguments, "query")
            person = arguments.get("person") or None
            limit = self._limit(arguments, 10)
            return _jsonable(
                service.search_conversations(query, person_ref=person, limit=limit)
            )

        if name == "get_interaction":
            session_id = self._require(arguments, "session_id")
            result = service.get_interaction(session_id)
            if result is None:
                raise ToolError(REASON_SESSION_NOT_FOUND)
            return _jsonable(result)

        if name == "get_person_profile":
            person = self._require(arguments, "person")
            result = service.get_person_profile(person)
            if result is None:
                raise ToolError(REASON_PERSON_NOT_FOUND)
            return _jsonable(result)

    # -- JSON-RPC plumbing -------------------------------------------------

    @staticmethod
    def _result_content(payload, is_error=False):
        content = {
            "content": [
                {"type": "text", "text": json.dumps(payload, default=str)}
            ]
        }
        if is_error:
            content["isError"] = True
        return content

    def _tool_error_content(self, message):
        return self._result_content(message, is_error=True)

    def handle_tools_call(self, params):
        params = params or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            result = self.call_tool(name, arguments)
        except ToolError as exc:
            return self._tool_error_content(str(exc))
        except Exception as exc:  # never leak a stack trace to the client
            reason = getattr(exc, "reason", None) or getattr(exc, "code", None)
            if reason:
                return self._tool_error_content(str(reason))
            return self._tool_error_content(
                "ERROR: %s: %s" % (type(exc).__name__, exc)
            )
        return self._result_content(result)

    def handle(self, message):
        """Handle one parsed message; return a response dict or None."""
        if not isinstance(message, dict):
            return None
        method = message.get("method")
        request_id = message.get("id")
        is_notification = request_id is None

        if method == "initialize":
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                },
            }

        if isinstance(method, str) and method.startswith("notifications/"):
            return None

        if method == "ping":
            if is_notification:
                return None
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}

        if method == "tools/list":
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": TOOL_DEFINITIONS},
            }

        if method == "tools/call":
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": self.handle_tools_call(message.get("params")),
            }

        if is_notification:
            return None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": ERR_METHOD_NOT_FOUND,
                "message": "Method not found: %s" % method,
            },
        }

    def run(self, in_stream, out_stream):
        for line in in_stream:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue  # malformed line: skip without crashing
            try:
                response = self.handle(message)
            except Exception:
                continue  # one bad message must not kill the loop
            if response is not None:
                out_stream.write(json.dumps(response) + "\n")
                out_stream.flush()


def main():
    server = InsightMcpServer()
    server.run(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
