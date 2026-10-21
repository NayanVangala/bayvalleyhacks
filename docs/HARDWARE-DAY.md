# HARDWARE-DAY.md

OnRecord hardware plan. Demo glasses are borrowed. Ray-Ban Meta Display with Neural Band. Setup at home on home WiFi. Never do first time setup at the venue.

Key facts:
- Capture: Meta Wearables Device Access Toolkit (DAT) 1.0 via iPhone bridge app forked from Findr
- Pin SDK 1.0.1 or later. 1.0.0 froze camera frames
- Glasses mic path through toolkit is experimental. Phone mic is the deterministic audio path. Glasses stay on photo and display duty unless the mic proves stable
- Neural Band: pinch and swipe only. No custom gestures. No raw EMG
- Glasses hold roughly 3 to 4 hours of use per charge. Charge only while docked in case. Never while worn
- Shutter sound and LED cannot be suppressed. Pitch that as a privacy feature

## 1. BEFORE PICKUP (message the friend)

- [ ] Confirm full day loan Oct 25
- [ ] Confirm setup day Oct 23 or Oct 24 at Nayan's home
- [ ] Friend backs up their own captures first
- [ ] Agree handover contents:
  - [ ] Glasses
  - [ ] Charging case
  - [ ] Cable
  - [ ] Neural Band
  - [ ] Neural Band charger if separate
- [ ] Agree friend unpairs from their phone at handover
  - [ ] Unpair in Meta AI app
  - [ ] Forget in Bluetooth settings

## 2. EARLY DE-RISK (do on Nayan's own phone BEFORE pickup day)

- [ ] Test Developer Mode toggle appears
  - [ ] Open Meta AI app
  - [ ] Settings, then App Info
  - [ ] Tap version number 5 times
  - [ ] Confirm Developer Mode toggle appears
  - [ ] Note: known bug, toggle never appears on some iOS builds. Test early on Nayan's own phone
- [ ] Create Meta Wearables developer account
- [ ] Create org in Meta developer console
- [ ] Create project in Meta developer console
  - [ ] Note: console may have an age gate (16+). Fallback is a parent's account for the console project
- [ ] Run Meta's CameraAccess sample end to end
  - [ ] Run on any available pair, or the simulator path the toolkit provides
  - [ ] Confirm photo capture works in the sample
- [ ] Decide transport and freeze it
  - [ ] Bluetooth first
  - [ ] Laptop hosted WiFi for photos
  - [ ] Write down the frozen choice. Do not change after this

## 3. HANDOVER + SETUP (at home, on home WiFi)

- [ ] Friend unpairs glasses on their phone
  - [ ] Unpair in Meta AI app
  - [ ] Forget in Bluetooth settings
- [ ] Factory reset if pairing fights back
- [ ] Pair glasses to Nayan's iPhone
- [ ] Run firmware update
- [ ] Enable Developer Mode on Nayan's phone
  - [ ] Meta AI app, Settings, App Info, tap version number 5 times
- [ ] Register bridge app once via Meta AI callback
- [ ] Verify app logs isDevMode == true before any session starts
- [ ] Install frozen bridge build
- [ ] Re-sign note: free Apple ID signing expires in about 7 days
  - [ ] Install final build Oct 24, not earlier in the week

## 4. FIRST-HOUR VERIFICATION

Run in this order. Check off each. Any failure stops the line. Fix it, or phone-only plan becomes primary.

- [ ] (1) isDevMode == true logged
- [ ] (2) Session starts, photo returns
  - [ ] Measure photo trigger to bytes latency at each resolution
  - [ ] Lock the setting used for enrollment photos
  - [ ] Write down locked resolution
- [ ] (3) Known-signal test on phone mic path
  - [ ] Record digits and claps
  - [ ] Transcript pitch and content correct
  - [ ] No drops over 60s
- [ ] (4) Neural Band test
  - [ ] Pinch fires the tag action
  - [ ] Swipe fires re-query
  - [ ] Fit is snug
  - [ ] False trigger test: type, wave, grip objects for 2 minutes, zero tags
- [ ] (5) Weak-link walk test
  - [ ] Walk to far room
  - [ ] Session survives, or fails over cleanly
- [ ] (6) Hinge-close test
  - [ ] Fold glasses, session tears down
  - [ ] Reopen glasses, restart works
- [ ] (7) Kill-switch drill
  - [ ] Kill glasses mid conversation
  - [ ] Phone-only mode live in under 30 seconds
  - [ ] Same session continues
- [ ] (8) Enroll one test speaker end to end
  - [ ] Verify speaker card on the lens

## 4A. XCODE VERIFICATION: THE THREE FIXED SPOTS (Oct 23/24)

Run all three on the setup day, on the real bridge build from Oct 24, on a real phone. The app, not the simulator. Open Xcode, Window, Devices and Simulators, open the device console (or run with the phone tethered and read the console in Xcode's Debug area). Success for each fix means a specific line in the Xcode console and a specific result server side. Any failure stops the line: rebuild, reinstall, re-verify.

### Fix A: hello with phone-generated UUID gets welcome, no 4404

What the fix does: the phone generates the session UUID itself, registers it over REST with POST /api/sessions (JSON body {"session_id": "<uuid>"}), THEN opens the WebSocket at ws://<host>:8000/ingest/audio?session_id=<same uuid> and sends hello as the first frame. The server only accepts the socket when the session already exists server side; before this fix, nobody created it, so every connect died with code 4404.

What to observe:
- Xcode console: the socket connects and no "WSClient: receive failed" line follows. The server replies with welcome; the session_id in welcome echoes the phone's UUID.
- Server side: POST /api/sessions returns the session containing your UUID before the socket ever opens.

What failure looks like:
- "WSClient: receive failed" with close code 4404, or the socket closing instantly after connect. Meaning: the phone opened the socket for a session the server never registered. Check that POST /api/sessions runs before the socket open and passes the same UUID in the body and the query string, and that hello's session_id matches the query string UUID.
- A welcome whose session_id does NOT match the phone's UUID. Meaning: the app let the server invent the session id. Fail the line; the next reconnect would resurrect a ghost session.

### Fix B: enrollment POSTs JSON base64 raw PCM (no WAV) to /api/enroll

What the fix does: the Swift enrollment screen posts JSON to POST http://<host>:8000/api/enroll with exactly three fields: name (text), photo_b64 (JPEG portrait, base64), voice_b64 (base64 of RAW PCM16 16kHz mono with NO 44-byte WAV header). Before this fix the app sent multipart form data with a WAV-wrapped voice clip, which the server no longer expects.

What to observe:
- Server responds 2xx with JSON containing person_id and the stored name.
- Xcode: the enrollment screen shows the person confirmed.

What failure looks like:
- 422 "enroll requires name, photo_b64 and voice_b64". A field is missing or null. Check the key names are exactly photo_b64 and voice_b64.
- 422 "invalid base64 payload". The base64 string is malformed. Do not URL-encode it.
- 413 "enrollment voice sample too large" or "enrollment photo too large". Trim the clip or shrink the photo and re-enroll.
- 503 "enrollment service is unavailable". The laptop's enrollment model did not load. Restart the server. Do not retry the enrollment until the server is healthy.
- Silent failure: 2xx, but the voice blob carries a WAV header (old build). The header gets embedded as if it were speech and voice matching silently degrades. If the install predates the fix, reinstall the Oct 24 build and re-enroll the speaker.

### Fix C: PREROLL flag 0x01 on the wire, partial pre-roll flagged not padded

What the fix does: pre-roll audio drained at talk start is sent as ordinary audio frames for the segment's seg_idx with the PREROLL bit 0x01 set in the binary header byte. The pre-roll buffer rarely holds an exact multiple of 100 ms; the last partial frame is sent short (not padded with silence), per PROTOCOL.md 5.1. Before this fix the app sent pre-roll with flag 0x04 and padded partial frames.

What to observe:
- Talk, release, and check the dashboard: the transcript covers speech from BEFORE the button press. That lead-in is the pre-roll working.
- Server side, the segment's first frames for seg_idx 0 carry flags with bit 0x01 set, in frameSeq order, then talk_start/talk_end bracket them.

What failure looks like:
- Pre-roll frames with flag 0x04 instead of 0x01. The old Swift build is installed. Rebuild from main. Note the server accepts 0x04 as a fallback hint, so the demo may still look fine, but the wire no longer matches PROTOCOL.md, which is the pinned spec.
- Partial frames padded to 3200 bytes with silence. Symptom: a dead lead-in in the transcript and drifting timestamps. Check the pre-roll drain path emits the short frame as captured, with the 0x01 flag, and never pads.

## 5. BATTERY DISCIPLINE

- [ ] Glasses live in plugged-in case whenever not on a face
- [ ] Charge to 100% the night before
- [ ] Event day: glasses come out for demo bursts only
- [ ] Top up at lunch
- [ ] Top up at 5pm
- [ ] Disable Meta AI auto-updates after Oct 23
- [ ] Freeze app builds after Oct 24 install
- [ ] iPhone on 20,000 mAh power bank, tethered
- [ ] iPhone Low Power Mode off

## 6. EVENT-DAY HARDWARE RULES

- [ ] Friend's phone Bluetooth OFF, or the phone stays home
  - [ ] Reason: friend's phone must not steal the connection
- [ ] Nobody else pairs to the glasses
- [ ] If glasses die mid demo, switch to phone-only mode
  - [ ] Phone-only switch is a rehearsed feature, not an apology

## 7. RETURN CHECKLIST (Oct 25 night or Oct 26)

- [ ] Back up any of the friend's remaining data, untouched
- [ ] Unpair from Nayan's phone only if the friend wants it
- [ ] Hand back all items:
  - [ ] Glasses
  - [ ] Case
  - [ ] Cable
  - [ ] Neural Band
  - [ ] Neural Band charger if separate
- [ ] Send thank-you
- [ ] Send photo of the glasses working at the event

## 8. XCODE VERIFICATION OF THE THREE WIRE FIXES

Do this on setup day in Xcode with the server running on the laptop, before the glasses go on. All three must pass before the first live glasses session. Any failure stops the line.

- [ ] (a) hello with phone-generated UUID gets welcome, no 4404
  - [ ] Arm a session in the app. In the Xcode console confirm the first text frame is hello and its session_id matches the phone-generated UUID in the ws:// query string
  - [ ] Confirm the server replies with welcome carrying the same session_id and ok: true
  - [ ] In the server log confirm the session exists server side before the socket opened: either the phone POSTed /api/sessions first, or the server registered the session on hello, whichever fix landed
  - [ ] Failure looks like: the socket closes immediately with code 4404, no welcome arrives, and the log shows UNKNOWN_SESSION. Cause is the session was never created server side, or the UUID in the query string and the hello body do not match. Fix: make the session-create REST call run before the socket opens, with the same UUID in both places

- [ ] (b) enrollment POSTs JSON base64 raw PCM to /api/enroll
  - [ ] In Xcode confirm the request is POST http://<host>:8000/api/enroll with Content-Type application/json. Not multipart, and the route is /api/enroll, not /enroll
  - [ ] Confirm the JSON body has name, photo_b64 (JPEG), and voice_b64 which is raw PCM16 16 kHz mono with no WAV header. The 44-byte RIFF/WAVE wrapper from wavWrapPCM must not be in this path
  - [ ] Confirm a 2xx response with person_id and name in the JSON body, and the person shows up in GET /api/people
  - [ ] Failure looks like: 422 "enroll requires name, photo_b64 and voice_b64" (wrong keys), 422 invalid base64, 413 payload too large, or 404 from posting to /enroll. A WAV-wrapped voice_b64 passes base64 validation, so the failure shows up later as a bad voice print instead of an error. Fix: verify the raw PCM path in EnrollmentAPI and confirm the first four decoded bytes are audio samples, not "RIFF"

- [ ] (c) PREROLL flag is 0x01 on the wire, partial pre-roll flagged not padded
  - [ ] Hold push-to-talk and confirm the pre-roll frames drained at talk start carry flags 0x01 (bit 0) in the 16-byte binary header. Not 0x04, not 0x00
  - [ ] Press talk within a second of arming, while the 10s buffer has not filled. Confirm the sealed segment still closes and the server records preroll_partial true with preroll_s under 10, and the audio is intact with no leading silence padding
  - [ ] Failure looks like: pre-roll frames with flags 0x04 (the old Swift assignment) or 0x00 (flag never set). The server treats flags as informational, so this will not hard fail, it just mislabels timing. A padded partial pre-roll looks like preroll_s reporting a full 10.0s when talk started early. Fix: one owner for the flag constant, and verify the state machine path, not just the encoder

## 9. FAILURE HANDLING

Written from the protocol spec and first principles. The InSight study notes (/tmp/insight-applied.md) never appeared, so this section is grounded in PROTOCOL.md section 6 and the review findings, not the study. The rules below are the ones to run.

- [ ] Keep-alive is on: the app pings at least every 15s. No pong within 30s means the socket is dead, do not keep talking into it
- [ ] Reconnect backoff is automatic: 1s, 2s, 4s, 8s, capped at 16s with jitter, no attempt limit while the session is live. On reconnect the app re-sends hello with the same session UUID and the server resumes, not restarts
- [ ] Offline staging covers dead air: while disconnected the app writes frames to the 500 MB disk ring in send order. After the handshake it replays oldest first and live frames wait behind replay. Watch the eviction counter in the app; if it moves, the ring filled and the oldest audio is gone for good
- [ ] Mid-demo drop drill: say nothing about the tech, tap to phone-only mode, keep the same session going. This is a rehearsed feature, not an apology, and it must be live in under 30s per the section 4 drill
- [ ] Hinge close equals teardown: folding the glasses ends the session path. Reopen, re-pair if needed, re-arm. Do not expect the old socket to resume after a fold
- [ ] Battery and heat: glasses live in the plugged-in case whenever not on a face. Charge to 100% the night before. Event day is demo bursts only, top up at lunch and at 5pm. If the frames feel hot, dock them and go phone-only until they cool. Never charge while worn
- [ ] Friend's phone Bluetooth stays off at the venue, or the phone stays home, so it cannot steal the glasses connection. One live socket per session: a reconnect supersedes the old socket, so do not run two clients into the same session UUID
- [ ] If the glasses mic path degrades, the phone mic is the deterministic fallback. Capture-source failover is not a reconnect: session ID, frameSeq, and the session clock all carry on
