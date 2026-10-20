# OnRecord (working name)

Conversation memory on Ray-Ban Meta Display glasses. Wear the glasses, talk
like normal, then ask "what did Maya say about the budget?" and get her actual
words back with a timestamp.

Design docs live outside this repo:
`~/workspace/bay-valley-hacks/insight-build-plan/` (BRIEF.md is the one-pager,
BUILD_PLAN.md Revision 2 is the full plan).

## Layout

- `server/` Python backend: ingest (FastAPI + WebSocket), transcription with
  confidence gating, face + voice identity fusion, Postgres/SQLite storage,
  ChromaDB vectors, MCP query server, Slack digest. See BUILD-STATUS.md for
  what is real vs stubbed.
- `dashboard/` Next.js dashboard (built by a separate coordinator).
- `bridge-ios/` iOS bridge app scaffold (built by a separate coordinator).
- `docs/` project docs (built by a separate coordinator).

## Run the server

```sh
make setup        # venv + deps
make run          # FastAPI ingest server on :8000
make mcp          # MCP server on stdio (separate terminal)
make test         # pytest suite
make seed         # rehearsal fixture data (REHEARSAL flag, never for judging)
```

Config comes from environment variables; see `.env.example`.

## Honesty notes

Heavy ML models are lazy-imported behind interfaces in
`server/onrecord/interfaces.py`. Without the ML extras installed, the server
runs on deterministic test doubles (fake embedders, fake transcriber) so the
pipeline and tests work anywhere. Stubs are marked as stubs in BUILD-STATUS.md.
