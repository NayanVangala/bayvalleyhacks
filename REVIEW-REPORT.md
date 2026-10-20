# REVIEW-REPORT: OnRecord senior review swarm

Date: 2026-10-20. Reviewers: 10 senior-engineer agents (workflow onrecord-review), each assigned a slice, each fixing bugs in their slice and committing. This report consolidates their findings, the coordinator's UI pass, and a blunt verdict for the Oct 25 demo.

## Verdict

NO-GO for the glasses demo path today. GO for the laptop-side pipeline.

The server, MCP query layer, dashboard, and digest all work end to end (verified live in the UI pass below, with test doubles for the ML models). But the iOS bridge and the server speak completely different wire protocols, so the glasses cannot complete a single session with this server as shipped. That is one focused fix, not a rebuild: PROTOCOL.md has been corrected to match the server, and the Swift client needs one rewrite pass against it. After that, one real-model run on Nayan's Mac (faster-whisper, ArcFace, ECAPA, real Chroma) is the remaining gate. Every critical bug class the reviewers found is fixed and covered by tests: the suite went from 0 tests at review start to 242 passing.

## The one blocker: three protocols, one system

Found independently by the iOS, realtime, and security reviewers.

- Handshake: the server has no hello/welcome. Sessions are created with POST /sessions, then the WebSocket connects with that session id or is closed (4404). The iOS app invents a UUID locally and never calls POST /sessions, so every connect is refused.
- Control vocabulary: server accepts arm, start, talk_stop, new_segment, pause, resume, photo, end. The bridge sends hello, session_start, talk_start, talk_end, photo_meta, session_pause, session_resume, session_end, ping, tag_moment. Zero overlap.
- Binary header: the server uses little-endian struct (seg_idx u32, frame_seq u32, capture_ts_ms u64 epoch ms, no magic, no kind, no flags). The bridge sends big-endian with magic bytes, a kind byte, flags, and a u32 session-relative timestamp. Both are 16 bytes by coincidence, so audio payloads land intact while every header field the server reads is garbage.
- Photos: the server expects a photo text control followed by a raw JPEG binary frame. The bridge sends a photo_meta JSON (rejected) followed by a headered frame that the server fed into the audio pipeline as PCM. No photo ever reached face identification.
- Enrollment: server is POST /api/enroll with JSON base64 (voice = raw PCM). The bridge posts multipart form data to /enroll with a WAV-wrapped voice file. The dashboard had the same multipart mistake (fixed, see Dashboard).
- State machines also diverge: iOS models per-press segments; the server models one continuous recording with segment boundaries. Pause, resume, and end transitions differ.

Action taken: PROTOCOL.md was corrected to describe the server as the canonical implementation (commit 6f6fac4 plus the realtime fixes). The Swift-side rewrite is deliberately NOT done by this swarm: it is a focused build task for the bridge, estimated at one session of work, and it must be verified on a Mac with Xcode, which this environment does not have.

## Per-area findings and fixes

### 1. Backend core (db, schema, Chroma, config, seed)

Fixed and pushed: b3cbe95 line of work landed as 9883bb5, b45a7e5, 21f45e7, b7704b8, a1bbbf6.

- Seed was not idempotent: a second run duplicated people and sessions, making name queries return AMBIGUOUS and breaking person-filtered demo queries. Seed is now fully idempotent, including completing a crashed partial run.
- db.py never rolled back failed statements; on Postgres one bad write would poison the shared connection for the life of the process. Rollback added everywhere; SQLite connections unified with foreign_keys ON, WAL mode, busy_timeout 5000 (the server and MCP server are separate processes on one DB file).
- Missing foreign keys on corrections speaker ids and slack_outbox session id let bogus audit rows in silently. Added.
- Fake vector store cosine used zip on mismatched dimensions, scoring partial-prefix vectors as confident matches. Dimension mismatch now scores 0.0.
- A pre-existing non-cosine Chroma collection was silently misread (distances converted as if cosine). Now raises a clear error; verified against real chromadb 1.5.9.
- Config accepted fatal values (threshold 1.5 or negative, port 99999, unnamed env parse errors). Settings now validate at construction and name the offending variable. SAMPLE_RATE env is honored.
- Text embedder output dimension is validated at the boundary (DIM_TEXT).

Left: the Postgres dialect path was never run against a live server (SQLite verified, dialect-shared code). Existing pre-fix databases do not gain the new foreign keys (CREATE TABLE IF NOT EXISTS); delete and recreate dev databases.

### 2. Transcription and gating

Fixed in 16690ef. Full suite green.

- CRITICAL: the STT factory silently fell back to FakeTranscriber on any real-model load failure, with only a stderr warning. The fake's canned segment gates as VERIFIED, so on any machine where faster-whisper fails to load, the system would store, embed, quote, and digest invented speech. Proven live on this VM. The fake now requires the explicit opt-in ONRECORD_STT=fake; real load failure raises.
- Language was pinned but never asserted. transcribe now checks info.language == "en" and language_probability >= 0.9 on every call, failing closed.
- The gate crashed on None metrics and failed open on NaN (NaN no_speech_prob could yield VERIFIED). Invalid metrics now land in REVIEW with low_conf set; thresholds and boundary behavior unchanged and probed exact (VERIFIED at avg_logprob >= -0.5, REVIEW band -1.0 to -0.5, SILENCE at no_speech_prob >= 0.6 or empty text).
- Model name validated English-only (.en variants); decode pinned as constants (temperature 0.0, beam 5, int8).
- Verified end to end: only VERIFIED non-empty rows reach the chunk store; REVIEW stores the literal "[uncertain]" display text with raw text server-only; digest and search cannot reach REVIEW or SILENCE content.

Left: the real faster-whisper path has never run (library not installed here). One real run on the Mac is required before the event.

### 3. Identity (enrollment, fusion, corrections)

Fixed in 9883bb5 (swept in by a parallel commit; content verified). 30 of 30 reviewer checks pass.

- CRITICAL: fusion ties returned a confident name. The matcher queried only the top 1 hit per modality, so two people enrolled with identical media both scored 1.0 and one was named. The matcher now queries top 2; a different person within 1e-6 of the top score makes the modality ambiguous and the result Unknown.
- CRITICAL: face and voice factories had the same silent fake fallback as STT. Fakes now require explicit opt-in (ONRECORD_FACE=fake, ONRECORD_VOICE=fake, ONRECORD_ALLOW_FAKE_EMBEDDERS=1, or rehearsal mode); otherwise RuntimeError, and ingest degrades to Unknown instead of dying.
- Partial enrollment could match: a failed voice upsert left a person with only a face print who could be named face-only. Enrollment is now validated before creation and rolls back prints and the person row on failure.
- Ghost prints: enroll_face and enroll_voice accepted nonexistent person ids and stored orphan prints the matcher could name. They now raise KeyError.
- Negative or NaN thresholds failed open (thresholds of -1.0 named unrelated media). Thresholds are validated finite in [0, 1] at construction.
- Corrections erased the original attribution evidence (face and voice scores reset to None). Scores are now preserved through assign and unassign; the correction log itself was verified genuinely append-only.
- Embedding dimensions validated at enrollment (face 512, voice 192); InsightFace output is checked and renormalized.

Left: real ArcFace and ECAPA never ran here (not installed); import boundaries and output validation are code-verified only. No margin rule beyond the exact-tie guard (a 0.98 runner-up against a 0.99 top hit still names the top hit when modalities agree); flagged as a design question, not silently changed. One adjacent flag for the Chroma owner: chroma_store converts a missing Chroma distance to score 1.0; if Chroma ever returns a None distance, the matcher sees a perfect score.

### 4. Query and MCP

Fixed in d8375e6, 387962a, 8dbd1c8. All 5 tools exercised live over stdio JSON-RPC against the seed; read-only verified by identical row counts before and after.

- CRITICAL: get_interaction returned every segment of a session, including REVIEW and SILENCE, breaking the verified-only rule every other path enforces. Fixed; sessions with no verified segments now return EMPTY with NO_VERIFIED_SEGMENTS.
- list_interactions_with_person silently resolved ambiguous names to the first enrolled person and counted unverified segments. Both layers fixed; ambiguous names return EMPTY / AMBIGUOUS_NAME with the candidate list and zero segments, everywhere.
- Semantic search presented unrelated quotes as receipts: a query whose only match was in a REVIEW segment returned unrelated VERIFIED quotes with score 0.0 and status OK. A relevance floor (cosine <= 0 is no similarity) and a blank-query guard are in. Note: with the real sentence-transformer embedder, unrelated queries can still score low-positive; there is no calibrated relevance threshold in the design. That is a decision for Nayan (see Decisions).
- False EMPTYs from pre-filter fetch limits (semantic fetched limit times 3 before filtering; exact fetched exactly limit). Both now scan a larger pool; wrong reason codes for unverified-only matches corrected to NO_VERIFIED_SEGMENTS.
- Blank exact term matched every segment (empty substring). Now EMPTY / NO_MATCHES.
- MCP stdio loop silently dropped malformed JSON and handler exceptions, hanging clients. It now replies with JSON-RPC parse and internal error codes carrying the request id; argument types validated and limits clamped to 100.
- Reason code naming: the codebase canonical codes (PERSON_NOT_FOUND, NO_MATCHES) were kept over the BUILD_PLAN wording (PERSON_UNKNOWN, NO_TOKEN_MATCH) so the dashboard and tests stay consistent. Same cases.

Left: list_people on an empty database returns a bare empty list with no reason code; that shape is pinned by the build's own tests. The exact-match backup is reachable at the service and REST layer only; the MCP toolset stays at exactly 5 tools by contract.

### 5. Realtime (WebSocket ingest, state machine, pre-roll)

Fixed in 9346994, 4d42971, 58ed73e, b0f4b7d, ec359be; PROTOCOL.md corrected in 6f6fac4. 19 new tests pass.

- As committed, the session pipeline was non-functional: the state machine accepted zero legal session flows, sessions created over HTTP were orphaned (the WS handler minted its own controllers), sealed segments were never processed at all, and ingest crashed on the first real JPEG. All fixed: end now seals and processes, audio before arm gets an explicit marker segment instead of a 409, binary frame parsing handles the nested PCM header (the old parser read garbage timestamps from PCM bytes), and the ingest path is behind per-message error containment so one bad frame cannot kill a session.
- Pause gaps are explicit records; partial pre-roll is flagged, never padded; the two-step end guard exists; sequence numbers survive reconnect.
- Audio gaps are marked in the timeline instead of silently stitched.

Left: the corrected PROTOCOL.md describes the server; the Swift client still implements the old incompatible variant (see The one blocker). Failover behavior is verified server-side only; the glasses-to-phone drill needs hardware.

### 6. Dashboard code

Fixed in 2f89cc2 (22 files). Typecheck and production build pass before and after; all bugs were runtime contract bugs the compiler could not see.

- The dashboard was written against an invented API and swallowed every failure silently. Session create and lifecycle used wrong routes and cast a wrapper object as a Session; segments and stats endpoints it called do not exist (404); enrollment sent multipart FormData to a JSON-only endpoint and always failed with 422; wire field names differed everywhere (speaker_name vs speaker_label, start_s vs start_offset_s), so transcript rows would have shown Unknown at 0:00 with NaN talk time.
- lib/api.ts was rewritten around normalization at the boundary (labels, offsets, source, created_at, the stored "ready" status mapped to ended, start_iso derived from session start plus offset). Getters now throw with server detail instead of returning fake empty lists, so the UI can no longer claim "No one enrolled yet" when the server is down.
- The live page now runs create, arm, start in sequence (the old Start button never armed or started anything), merges sealed segments, upserts by segment id (tap-to-tag corrections now render; previously dedupe dropped them), filters by session, handles the segments batch and session_ended message shapes, and polls REST every 3 seconds as a fallback because the server has no /ws/live endpoint.
- SessionControls rendered no buttons at all for armed sessions; fixed, with an End escape hatch for unknown statuses. SearchPanel handles null reasons, AMBIGUOUS_NAME, missing timestamps, stale overlapping responses, and disables Search on an empty query. EnrollmentForm: voice upload is reachable with a working mic, submit is disabled with an explanation while recording, unmount stops the recorder, missing name or photo produce real errors. Status Refresh shows progress and settles health and stats independently.
- Verified live against a real server by the reviewer: full lifecycle, enrollment 200, aggregated stats, EMPTY / NO_MATCHES search shape, error detail surfacing, all four routes 200 in a clean production build.

Left (features and server gaps, not correctness bugs): no server /ws/live broadcast endpoint; no tap-to-tag correction control in the dashboard (the server endpoint exists); Chroma collection counts stay "unknown" on the status page (no endpoint exposes them); dashboard delete-person button missing (backend exists, see Security).

### 7. iOS bridge (static review, cannot compile here)

Fixed in 41e0b39 and 11ff569 (12 certain bugs). Conservative fixes only; uncertain items flagged, not rewritten.

- PREROLL flag value collided with SEGMENT_START; hello omitted required fields and used the wrong app id; ping used the wrong timestamp key; talk_end sent duration_s instead of duration_ms; session_end used an illegal reason value; segmentIndex incremented at talk start (first segment was 1, spec says 0); pre-roll frames were emitted before their talk_start; reconnect backoff reset on every attempt (pinned at 1s forever) and capped at 16s instead of 30s.
- Fresh installs defaulted the server host to 127.0.0.1 (the phone pointing at itself) in two places; fixed to the documented 192.168.2.1. Force-unwrapped URLs in Config could crash on a bad host string.
- PhoneSource.stop() and MockSource.stop() permanently finished their streams while SessionManager reuses one source for the app lifetime: every second session, and every failover drill, would have failed. Fixed.
- Verified good: pre-roll is 10s and RAM-only; the CaptureSource abstraction is clean (SDK imports only in GlassesSource and DATRegistration); the phone fallback path is complete with session id and connection surviving failover; no hardcoded secrets (ClientToken comes from build settings, empty in the committed xcconfig).
- Open: offline staging orphans the ended session's staged files on disk forever (including its own session_end), never replayed and never deleted; a replay ordering race can let a live send jump ahead of staged frames; drop counters underreport. Two sources of truth for the server address (Settings vs Info.plist build settings) can split the WebSocket and enrollment hosts.

Cannot be verified without a Mac: compilation, all Meta DAT 1.0 API names, XCTest results, and real WebSocket behavior.

### 8. Tests

- Before: 0 tests at review start (server/tests was a .gitkeep). The build swarm's own 208-test suite landed during the review (e3919cc). Review total: 242, all passing, verified again by the coordinator on the final tree including the security commit.
- The tests reviewer added 34 demo-contract tests (b5b98dd): fusion threshold boundaries and Unknown paths, digest checker invention cases, retrieval reason codes and ambiguity, state machine illegal transitions, pre-roll after an empty pause.
- Two production bugs were found by those tests and fixed in f0136c7: the digest checker had no proper-noun containment (an invented name in a recap passed validation), and ambiguous-name results carried no candidates.
- Setup bug: numpy was missing from dev requirements although the test doubles import it; added.
- Still untested: all real ML paths (by design here), the MCP fallback resolver (now fixed via QueryService, see section 4), Swift tests (cannot run), and any live glasses, phone, or Slack webhook end-to-end test. Slack is covered through the file sink and a mocked webhook failure.

### 9. Security and privacy

No secrets found anywhere, including full git history; no rotation needed. Env examples hold placeholders only; bridge tokens come from build settings only.

Fixes (committed in 19a7019 after the tree settled; verified live by the reviewer before commit):

- HIGH: the server bound 0.0.0.0 with no authentication on any endpoint. On venue WiFi anyone could read all transcripts, enroll a face and voice print, correct speakers, or trigger a confirmed Slack digest. Bind is now the HOST env setting, default 127.0.0.1, with docs warning to use the laptop address on the private hosted network only; CORS is locked to the dashboard origins on localhost, no credentials.
- HIGH: person deletion was promised in the enrollment guide ("deleted on the spot") but did not exist in code at all. Backend delete is now implemented end to end (store rows, corrections, face print, voice print, transcript chunks, orphan cleanup) with DELETE /api/people/{id}; verified live (enroll, delete, gone, second delete 404). The dashboard button is still missing (see Decisions).
- Slack webhook URL was trusted blindly; a mistyped or malicious value would exfiltrate verbatim quotes. Posts are now refused unless the URL is https with hostname exactly hooks.slack.com.
- Input hardening: search and MCP limits clamped, WebSocket text frames capped at 64 KB, audio at 1 MB, photos at 10 MB with JPEG magic-byte checks, enrollment payloads type-checked, base64 validated, size-capped with 413s. Bridge photo frames are routed to the photo path server-side, closing the JPEG-as-audio confusion for the real bridge client.
- Verified clean: all SQL is parameterized; exact search escapes LIKE wildcards; no FTS string reaches a query; the MCP server exposes no write tools and does not leak stack traces; rehearsal fixtures are quarantined behind ONRECORD_REHEARSAL with fictional people; raw enrollment media is discarded server-side after embedding; raw_text is stripped from every API payload.
- Data at rest: Chroma prints, database rows (including raw_text), Slack outbox bodies, and the phone's offline staging (up to 500 MB of raw audio frames and JPEGs) persist indefinitely, unencrypted, with no retention limit. FileVault and the phone passcode are the only protections. This needs a retention decision (see Decisions).

### 10. Integration and E2E

- OnRecord golden path: the query reviewer exercised all 5 MCP tools over real stdio against the seed (enroll state, session, verified segments, verbatim quote with timestamp, read-only proof); the dashboard reviewer ran the full HTTP lifecycle and enrollment against a live server; the coordinator's UI pass (next section) re-verified the assembled system after all fixes.
- Fixed along the way by the E2E pass and folded into the sections above.

## UI pass (coordinator, functional only, zero styling)

Setup: server booted on port 8000 with the rehearsal seed and explicit test doubles (ONRECORD_STT=fake, ONRECORD_FACE=fake, ONRECORD_VOICE=fake, rehearsal flag on); dashboard production/dev server on port 3000 as left by the dashboard reviewer. All four routes returned 200: /, /people, /search, /status.

- People: both seeded people returned with the fields the UI normalizes (name, created_at).
- Search with results: "what did Maya say about the budget" filtered to Maya Chen returned her verbatim quote ("Let us lock the budget for the event. The venue budget is 2400 dollars total...") with ISO start and end timestamps and a positive score.
- Search empty: gibberish query returned status EMPTY, reason NO_MATCHES, empty results, and the candidates field the panel renders. No blank panel, no fake success.
- Session lifecycle through the dashboard's routes: create, arm, start, pause, resume, end all returned the wrapper with correct statuses (armed, recording, paused, recording, ready-mapped-to-ended). No dead controls in the flow.
- Enrollment: JSON enroll with a real image returned 200 and the person record; a deliberately undecodable photo payload returned 422 (correct validation, and the form surfaces server detail for it). 
- Digest dry run (confirm false): returned a POSTABLE digest with correct counts (VERIFIED 8, REVIEW 0, SILENCE 0), talk time per speaker, and topics computed from verified text only.
- Delete: DELETE /api/people/{id} returned 200 and the person no longer appears in the people list.
- CORS: the dashboard origin receives the allow header; a foreign origin receives none.

No functional UI bugs were found in this pass that the dashboard reviewer had not already fixed. Remaining functional gaps are features and decisions, listed below, not bugs: no correction (tap-to-tag) control, no delete button, live updates by 3 second polling until a /ws/live endpoint exists.

One process note: the earlier accidental kill of a dev ingest server on port 8000 (dashboard reviewer) was not repeated; the UI pass server ran on a scratch data dir and was stopped afterward, and the port 3000 dashboard was left running.

## Lexia (feature branch, NOT merged)

Lexia is a standalone Read Mode service (OCR, reading chunking, TTS) living only on branch feature/lexia-read-mode. It is deliberately removable: main contains no Lexia code (an accidental landing on main during the build was reverted in fed8b78), and nothing in this report's main-branch verdict depends on it.

- Reviewed in a separate worktree off the feature branch; fixes were committed and pushed to feature/lexia-read-mode only (branch head c6cfe25, including the TTS fix from this review).
- API contract: POST /read (multipart image) creates a reading session and returns extracted text plus chunks; GET /read/{id} returns the session; POST /read/{id}/speak produces audio for the session text. Service runs on port 8100.
- Golden path verified with REAL OCR: with tesseract 5.3.4 installed, /health reports ocr_engine "tesseract". A rendered test image ("The venue budget is 2400 dollars total. / Catering is capped at 900 dollars. / Please send the final breakdown by Friday.") came back word for word, mean_confidence 0.956 (the real engine's confidence, not a canned value), chunked into the correct 3 chunks, and the speak call completed the chain.
- TTS is the one test double left in that chain: the fake TTS engine produced the WAV (7.69s). Local TTS (espeak) is untested; no espeak on this VM.
- Verdict for Lexia: demo-ready for the reading path on any machine with tesseract installed. It does not block, and is not blocked by, the OnRecord demo path.

## What remains stubbed or hardware-blocked

- Real ML models have never run anywhere in this environment: faster-whisper, InsightFace/ArcFace, ECAPA, SentenceTransformer, and real ChromaDB persistence are all verified through test doubles only. ChromaDB the library was exercised in a scratch venv by the backend reviewer (collections, cosine, reuse) but not as the server's live store.
- Postgres path untested against a live server.
- The Swift bridge cannot compile here; every DAT 1.0 API name is unverified against the real SDK headers.
- Glasses capture, Neural Band input, failover drills, and the lens speaker card are all hardware-day work (Oct 23/24), untouched by this review.
- Enrollment audio format: the dashboard sends browser-recorded audio bytes as voice_b64, which the server treats as raw PCM. The fakes accept anything; the real ECAPA embedder may reject non-PCM audio.
- Slack posting verified via file sink and mocked failures only; no real webhook post was made.

## Decisions Nayan needs to make

1. Protocol: the server is now canonical (PROTOCOL.md corrected). Approve the Swift client rewrite against it, or declare the bridge spec canonical and move the server instead. Nothing else unblocks the glasses path.
2. Semantic relevance threshold: with the real embedder, low-positive scores on unrelated queries will still return quotes. Pick a floor after one real-model test session, or accept the behavior and keep the exact-match path as the demo fallback.
3. Authentication: accept LAN trust strictly on the laptop-hosted private AP (current state), or add a shared token for bridge, dashboard, and server before the event. Never run the server on venue WiFi either way.
4. Pre-roll disclosure: the 10 second pre-roll captures speech from before the press, including bystanders. Either the consent script in docs/ENROLLMENT-GUIDE.md says so, or the buffer shrinks.
5. Slack versus consent wording: the consent script says prints stay on the laptop; the digest posts verbatim quotes to Slack cloud when a webhook is set and confirmed. For the demo: file sink only, or reword the script.
6. Retention: pick a wipe ritual (for example, delete data/ and phone staging after the event) and whether deleting a person should anonymize their segments inside other people's sessions instead of deleting the segments outright (current behavior).
7. Dashboard delete button: the backend exists and the enrollment guide promises dashboard-confirmed deletion. Add the button (small feature) or reword the guide.

## Go / no-go for Oct 25

- Laptop pipeline (server, MCP, dashboard, digest): GO, conditional on one real-model run on the Mac (whisper transcription, one real enrollment with ArcFace and ECAPA, real Chroma) before Oct 24 freeze.
- Phone-only fallback demo: GO on the same condition; the HTTP and dashboard path the fallback uses is the path verified in this review.
- Glasses demo path: NO-GO until the Swift client speaks the corrected protocol and is verified against the server on the Mac, then on the borrowed Display pair on setup day.
- Lexia read mode: GO on the feature branch with tesseract installed; independent of the above.

Test summary: 0 tests at build handoff, 208 from the build swarm's late suite, 242 passing at the end of this review (0 failures), re-verified by the coordinator after the final security commit (19a7019).
