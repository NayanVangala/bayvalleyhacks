# BUILD-STATUS

Maintained by the server build coordinator. Last updated: 2026-10-20 (build in progress).

## Ownership

- `server/`, `server/tests/`, root scaffold: server coordinator (this file).
- `dashboard/`: dashboard coordinator (separate).
- `bridge-ios/`: bridge coordinator (separate).
- `docs/`: docs coordinator (separate).

## Phase status

- Phase 0 Scaffold: DONE (layout, interfaces contract, Makefile, env example).
- Phase 1 Backend core (schema, storage, Chroma, config): IN PROGRESS.
- Phase 2 Transcription (whisper service, gating, state machine, WS ingest): IN PROGRESS.
- Phase 3 Identity (enrollment, fusion, corrections): IN PROGRESS.
- Phase 4 Query (MCP server, semantic search, exact-match backup): IN PROGRESS.
- Phase 5 Surfaces: dashboard + iOS bridge owned by sibling coordinators. Slack digest poster lives in server/ and is tracked here.
- Phase 7 Tests + seed fixtures: PENDING.

## Real vs stubbed

- Filled in as modules land. Rule: anything not wired to a real model is
  labeled a test double or stub here and in its module docstring.
