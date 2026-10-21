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

- [ ] (a) hello with phone-generated UUID gets welcome, no 4404
  - [ ] Arm a session in the app. In the Xcode console confirm the first text frame the phone sends is hello and its session_id matches the phone-generated UUID in the ws:// query string
  - [ ] Confirm the server replies with welcome carrying the same session_id and ok: true. If welcome carries a different session_id, the app let the server invent it: fail the line, the next reconnect would resurrect a ghost session
  - [ ] Failure looks like: the socket closes immediately with code 4404, no welcome arrives, or "WSClient: receive failed" in the console. Cause is the session was never created server side, or the UUID in the query string and the hello body do not match. Fix: the phone must POST /api/sessions (body {"session_id": "<uuid>"}) before opening the socket, with the same UUID in the REST body, the query string, and hello

- [ ] (b) enrollment POSTs JSON base64 raw PCM (no WAV) to /api/enroll
  - [ ] In Xcode confirm the request is POST http://<host>:8000/api/enroll with Content-Type application/json. Not multipart, and the route is /api/enroll, not /enroll (posting to /enroll returns 404)
  - [ ] Confirm the JSON body has exactly name, photo_b64 (JPEG portrait), and voice_b64 which is base64 of raw PCM16 16 kHz mono with no WAV header. The 44-byte RIFF/WAVE wrapper from wavWrapPCM must not be in this path. Verify the first four decoded bytes are audio samples, not "RIFF"
  - [ ] Confirm a 2xx response with person_id and the stored name in the JSON body, and that the person shows up in GET /api/people
  - [ ] Failure looks like: 422 "enroll requires name, photo_b64 and voice_b64" (wrong keys or null field), 422 invalid base64, 413 payload too large, 503 enrollment service unavailable (restart the server and re-enroll). A WAV-wrapped voice_b64 passes base64 validation, so that failure shows up later as a bad voice print, not an error

- [ ] (c) PREROLL flag is 0x01 on the wire, partial pre-roll flagged not padded
  - [ ] Hold push-to-talk and confirm the pre-roll frames drained at talk start carry flags 0x01 (bit 0) in the 16-byte binary header. Not 0x04, not 0x00
  - [ ] Press talk within a second of arming, while the 10 s buffer has not filled. Confirm the sealed segment still closes and the server records preroll_partial true with preroll_s under 10, and the audio is intact with no leading silence padding
  - [ ] Failure looks like: pre-roll frames with flags 0x04 (the old Swift assignment) or 0x00 (flag never set). The server treats flags as informational, so this will not hard fail, it just mislabels timing. A padded partial pre-roll shows up as preroll_s reporting a full 10 s when talk started early. Fix: one owner for the flag constant in the Swift code, and verify the state machine path, not just the encoder

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

## 7. STREAM FAILURE HANDLING (InSight lessons)

InSight shipped with zero reconnect or keep-alive logic and died on drops mid event. Our stack answers every gap they had. The job on setup day is to verify the answers actually work, not to rebuild them.

### What to watch in the Xcode console

- The app pings the server every 15 s. A quiet console with no pong for 30 s means the link is dead: the app logs "WSClient: no pong for over 30 s, treating as drop" and starts reconnecting on its own.
- Errors on the capture path are never silent. GlassesSource turns every SDK error into a health snapshot. If something fails, it says so in the console. Read it; do not guess.

### If the stream drops mid demo

1. Do nothing for 30 seconds. The app retries with backoff (1, 2, 4, 8, 16 s, jittered, no attempt limit) using the SAME session UUID, so the server resumes the session instead of starting a new one. Do not re-arm the session. Do not kill the app.
2. Capture keeps running on the phone while disconnected. Unsent frames stage to the 500 MB offline ring and replay oldest first after the reconnect, with live frames queued behind them. The server dedupes on session plus segIdx plus frameSeq, so a drop costs seconds of liveness, not the recording.
3. If the drop lasts past ~30 s of retries: switch to phone-only capture. The socket, session ID and session clock carry on; only the audio source changes. This is the rehearsed kill-switch drill in section 4, item 7.
4. If the server itself is unreachable (no pongs ever, POST /api/sessions times out): the laptop slept or the process died. Wake the laptop, restart the server, confirm POST /api/sessions still answers for your UUID. The phone reconnects on its own.
5. If pairing breaks at the venue: do not re-pair. Phone-only is the plan. Re-pairing at the venue is a non-goal per the fallback doc.

### Thermal and battery discipline

- Glasses hold 3 to 4 hours per charge (section 5). Heat throttles the radio and CPU, so keep the glasses shaded between demo bursts, dock them in the plugged-in case to cool, and never charge them while they are hot. Stills only; video is banned for this reason.
- Keep the laptop awake. A sleeping laptop kills pings and the socket dies on both ends. Disable sleep on AC power for the whole event day.
- Keep the iPhone on the 20,000 mAh power bank, tethered, Low Power Mode off (section 5).
- If the frames feel hot during a session, dock them and go phone-only until they cool.

### Drills to run on setup day (add to section 4)

- [ ] Wi-Fi drop during replay: disconnect the phone Wi-Fi for 60 s mid conversation, reconnect, and confirm the transcript has no gap and no duplicated segments
- [ ] Mid-demo drop drill: say nothing about the tech, tap to phone-only mode, keep the same session going. A rehearsed feature, not an apology. Live in under 30 s per the section 4 drill
- [ ] Watch the offline staging eviction counter in the app during the drills. If it moves, the 500 MB ring filled and the oldest audio is gone for good
- [ ] Hinge close equals teardown: folding the glasses ends the session path. Reopen and re-arm. Do not expect the old socket to resume after a fold
- [ ] Wrong-audio-source check: verify the runtime 16 kHz assert fires on the audio path, and that the HFP 8 kHz fallback is not silently active. If the glasses mic ever produces 8 kHz frames, phone mic takes over. This is the exact fiasco that killed InSight's audio
- [ ] Long-segment guard: a crash between talk_start and talk_end must not leave a segment open forever. Confirm the server force-seals an idle segment after 5 minutes
- [ ] One live socket per session: a reconnect supersedes the old socket, so never run two clients into the same session UUID

## 8. RETURN CHECKLIST (Oct 25 night or Oct 26)

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
