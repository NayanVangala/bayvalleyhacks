# BUILD-STATUS

Maintained by the server build coordinator. Last updated: 2026-10-20, end of the AFK build window.

## Ownership

- `server/`, `server/tests/`, root scaffold: server coordinator (this file).
- `dashboard/`: dashboard coordinator (separate).
- `bridge-ios/`: bridge coordinator (separate).
- `docs/`: docs coordinator (separate).
- `lexia/`: Lexia (Read Mode) coordinator (separate). Standalone service on port 8100. Future integration: the OnRecord server may add a proxy route to Lexia later; not built now, by instruction.

## Phase status (server scope)

- Phase 0 Scaffold: DONE. Layout, interfaces.py contracts, Makefile, env example.
- Phase 1 Backend core: DONE. config, Postgres + SQLite schema, Store (SQLite default, lazy psycopg), Chroma store with in-memory fake, text embedders.
- Phase 2 Transcription: DONE. Confidence gating, push-to-talk state machine with 10s pre-roll, STT service, FastAPI + WebSocket ingest (16-byte binary header). Note: a sibling hardening pass (commit 16690ef) made STT fail closed with no silent fake fallback and asserted decode language; read stt.py header for current behavior.
- Phase 3 Identity: DONE. Enrollment (face + voice prints into the vector store), fusion matcher (agree or Unknown), append-only corrections, end-to-end pipeline.
- Phase 4 Query: DONE. QueryService (semantic search with verbatim quotes and timestamps, exact-match backup, no-silent-empty reason codes) and insight-mcp (stdio JSON-RPC, 5 read-only tools).
- Phase 5 Surfaces (server side): DONE. Slack digest (fixed template over VERIFIED segments only) with deterministic citation checker and human-confirmed poster. Dashboard and iOS bridge are sibling-owned.
- Phase 7 Tests + seed: DONE. 208 tests in server/tests/ written by this swarm, all passing (see below). seed.py rehearsal fixture executed live: enrolls 2 people, loads an 8-segment budget conversation, refuses to run without the REHEARSAL flag.

## Test results (2026-10-20)

- This swarm's suite: 208 tests, all passing. Files: test_store (71), test_gating (42), test_state_machine (28), test_digest + test_mcp (22), test_ingest (12), test_fusion (11), test_retrieval (11), test_corrections + test_pipeline (11).
- Combined run including sibling-owned tests: 236 passed, 4 to 6 failing, all inside tests/test_demo_contracts.py (not written by this swarm). The failures are recap-checker contract tests: they require the digest citation checker to also reject invented proper nouns and invented dates/months in the recap slot (BUILD_PLAN Section 2.7 proper-noun and date containment rules). digest.py currently checks citations, number containment, and quote fidelity only. KNOWN GAP: add proper-noun and date-token containment to validate_recap in digest.py, then those contract tests should pass. Not fixed during this window because sibling coordinators had large uncommitted edits across server/ in flight at the same time.

## Real vs stubbed

REAL (working logic, tested):
- Storage (SQLite fully; Postgres via the same SQL with psycopg when DATABASE_URL is set, not integration-tested on this VM: no Postgres server here).
- Session state machine, pre-roll ring buffer, confidence gating.
- Pipeline wiring: transcribe, gate, fuse, store, embed VERIFIED chunks.
- Retrieval contract, MCP protocol, digest template and checker (minus the proper-noun rule above), Slack poster (webhook and file sink both exercised in tests).
- FastAPI REST and WebSocket ingest, exercised end to end with TestClient.

TEST DOUBLES (deterministic fakes behind the real interfaces, used automatically when ML deps are absent):
- FakeTranscriber (scripted), FakeFaceEmbedder, FakeVoiceEmbedder (identical input gives cosine 1.0; NOT recognition), HashingTextEmbedder (token overlap, not semantics), FakeVectorStore (in-memory cosine).

REAL BUT UNTESTED ON THIS VM (code written, lazy imports, needs the demo laptop):
- FasterWhisperTranscriber (faster-whisper small.en int8), InsightFaceEmbedder (ArcFace buffalo_l), ECAPAEmbedder (speechbrain), ChromaVectorStore (chromadb), SentenceTransformerEmbedder. Install with: pip install -e "server[ml]" equivalent (see server/pyproject.toml extras).

## Top blockers for hardware day (Oct 23/24)

1. Install ML extras on the MacBook and smoke-test the real models end to end (whisper speed on M4, ArcFace on a glasses photo, ECAPA on glasses audio). Thresholds (face 0.45, voice 0.75) are starting values from the plan, not measured; calibrate with the actual demo speakers during enrollment.
2. Pair the borrowed Display glasses, enable dev mode, and verify the bridge WS protocol against ingest (bridge is sibling-owned; protocol lives in docs/ and ingest.py docstring).
3. Implement the digest proper-noun/date containment rule to close the demo-contract failures.
4. Postgres: either keep SQLite (simpler, tested) or stand up Postgres.app and run the suite against DATABASE_URL once.
5. Confirm the loan (full day Oct 25 plus setup day) and pick the final project name (working name OnRecord).
